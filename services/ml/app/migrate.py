"""Apply ARGUS SQL migrations (ledgered, checksummed, never boot-blocking).

BOOT-SAFETY CONTRACT
--------------------
A database outage must never stop the API from becoming healthy:

* every connection uses a bounded ``connect_timeout`` (default 5 s,
  override with ``ARGUS_DB_CONNECT_TIMEOUT``);
* **this module never calls sys.exit(1) on a database problem unless
  fail-fast is explicitly requested** -- the default is a loud, non-fatal
  warning so ``start.sh`` can always ``exec uvicorn`` and ``/health`` can
  answer (reporting database reachability honestly) while the DB is down;
* ``MIGRATIONS_FATAL=1`` restores fail-fast (exit 1). That is the right
  setting for a Render ``preDeployCommand`` step, where a failed migration
  SHOULD block the release instead of half-deploying; it is the wrong
  setting for the container entrypoint.
* ``ARGUS_MIGRATE_OPTIONAL=1`` forces non-fatal regardless of
  ``MIGRATIONS_FATAL``.

LEDGER (migration 009 creates the same table create-if-missing here)
--------------------------------------------------------------------
``schema_migrations(filename PRIMARY KEY, checksum, status, detail,
applied_at)`` records, per file, the sha256 of the bytes that were applied:

* a file whose recorded checksum matches its current bytes is skipped;
* a file whose bytes CHANGED after being applied is reported as **DRIFT**
  and left untouched (it is never silently re-applied);
* a file that failed is not recorded, so the next run retries it;
* 007_roles.sql needs a superuser (``CREATE ROLE``) and is recorded as
  ``status='skipped'`` with the reason instead of being retried forever
  (``ARGUS_RETRY_SKIPPED=1`` forces a retry).

Each file runs inside a transaction together with its ledger row, so a
failure mid-file leaves neither a partially applied schema nor a record.

This module is import-safe: all work happens inside functions, so
``from app.migrate import probe_database`` (used by ``/health``) has no
side effects.
"""
from __future__ import annotations

import glob
import hashlib
import os
import sys

import psycopg

LEDGER_TABLE = "schema_migrations"
# 007 needs CREATE ROLE (superuser); unavailable on managed Postgres.
SKIP_FILES = {"007_roles.sql"}
DEFAULT_CONNECT_TIMEOUT = 5.0

BASE_CANDIDATES = [
    "/srv/argus/db",  # container layout
    os.path.join(  # repo layout: <repo>/db
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))),
        "db",
    ),
]

LEDGER_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    filename   TEXT PRIMARY KEY,
    checksum   TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'applied',
    detail     TEXT,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE {LEDGER_TABLE} ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'applied';
ALTER TABLE {LEDGER_TABLE} ADD COLUMN IF NOT EXISTS detail TEXT;
"""


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------
def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def fatal_mode() -> bool:
    """Should a migration/drift problem block the caller (exit 1)?

    Precedence: ``ARGUS_MIGRATE_OPTIONAL=1`` -> never fatal;
    otherwise an explicit ``MIGRATIONS_FATAL`` wins; otherwise non-fatal.
    Non-fatal is the default because the entrypoint must not be able to
    keep ``/health`` from answering.
    """
    if _flag("ARGUS_MIGRATE_OPTIONAL", False):
        return False
    if os.environ.get("MIGRATIONS_FATAL") is not None:
        return _flag("MIGRATIONS_FATAL", False)
    return False


def connect_timeout() -> float:
    try:
        return float(os.environ.get("ARGUS_DB_CONNECT_TIMEOUT",
                                    DEFAULT_CONNECT_TIMEOUT))
    except (TypeError, ValueError):
        return DEFAULT_CONNECT_TIMEOUT


def _db_url(db_url: str | None = None) -> str | None:
    return db_url or os.environ.get("DATABASE_URL") or None


def _base_dir() -> str:
    return next((b for b in BASE_CANDIDATES if os.path.isdir(b)),
                BASE_CANDIDATES[0])


def _migration_files(mig_dir: str) -> list[str]:
    return sorted(glob.glob(os.path.join(mig_dir, "*.sql")))


def _checksum(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# ---------------------------------------------------------------------------
# Connectivity probe (used by /health; never raises)
# ---------------------------------------------------------------------------
def probe_database(db_url: str | None = None, timeout: float | None = None) -> bool:
    """True when a real ``SELECT 1`` round-trip succeeds. Never raises."""
    url = _db_url(db_url)
    if not url:
        return False
    try:
        with psycopg.connect(url, connect_timeout=timeout or connect_timeout()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                return cur.fetchone() == (1,)
    except Exception:  # noqa: BLE001 - a probe must never break the caller
        return False


def db_status(db_url: str | None = None, timeout: float | None = None) -> dict:
    """Honest database status for ``/health``: configured / reachable / error."""
    url = _db_url(db_url)
    if not url:
        return {"configured": False, "reachable": False,
                "error": "DATABASE_URL is not set"}
    try:
        with psycopg.connect(url, connect_timeout=timeout or connect_timeout()) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return {"configured": True, "reachable": True, "error": None}
    except Exception as exc:  # noqa: BLE001
        return {"configured": True, "reachable": False,
                "error": f"{type(exc).__name__}: {exc}".splitlines()[0][:200]}


# ---------------------------------------------------------------------------
# Migration runner
# ---------------------------------------------------------------------------
def _ledger(conn) -> dict[str, tuple[str, str]]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT filename, checksum, status FROM {LEDGER_TABLE}")
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def _record(conn, filename: str, checksum: str, status: str,
            detail: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO {LEDGER_TABLE} (filename, checksum, status, detail,
                                            applied_at)
                VALUES (%s, %s, %s, %s, now())
                ON CONFLICT (filename) DO UPDATE
                    SET checksum = EXCLUDED.checksum,
                        status = EXCLUDED.status,
                        detail = EXCLUDED.detail,
                        applied_at = now()""",
            (filename, checksum, status, detail))


def run_migrations(db_url: str | None = None, mig_dir: str | None = None,
                   seed: str | None = None, timeout: float | None = None,
                   log=print) -> dict:
    """Run every pending migration. Returns a structured summary; raises nothing."""
    summary = {
        "ok": False, "ledger_ready": False, "connected": False, "error": None,
        "applied": [], "skipped": [], "drift": [], "failed": [],
        "skipped_unsupported": [],
    }
    url = _db_url(db_url)
    if not url:
        summary["error"] = "DATABASE_URL not set - skipping migrations"
        log("migrate: DATABASE_URL not set - skipping migrations")
        return summary

    base = mig_dir or os.path.join(_base_dir(), "migrations")
    seed_path = seed or os.path.join(_base_dir(), "seeds",
                                     "calibration_maps_seed.sql")
    files = _migration_files(base)
    if not files:
        summary["error"] = f"no migration files in {base}"
        log(f"migrate: no migration files in {base} - skipping")
        return summary

    retry_skipped = _flag("ARGUS_RETRY_SKIPPED", False)
    tmo = timeout or connect_timeout()

    try:
        conn = psycopg.connect(url, autocommit=True, connect_timeout=tmo)
    except Exception as exc:  # noqa: BLE001
        summary["error"] = (f"cannot connect to the database "
                            f"(connect_timeout={tmo}s): "
                            f"{type(exc).__name__}: {exc}").splitlines()[0][:300]
        return summary

    with conn:
        summary["connected"] = True
        try:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(LEDGER_DDL)
            summary["ledger_ready"] = True
        except Exception as exc:  # noqa: BLE001
            summary["error"] = (f"cannot create the migration ledger: "
                                f"{type(exc).__name__}: {exc}").splitlines()[0][:300]
            return summary

        recorded = _ledger(conn)
        log(f"migrate: running {len(files)} migrations "
            f"(ledger holds {len(recorded)} rows)")

        for path in files:
            name = os.path.basename(path)
            digest = _checksum(path)
            prior = recorded.get(name)
            if prior and prior[0] == digest:
                status = prior[1]
                if status == "skipped" and not retry_skipped:
                    summary["skipped_unsupported"].append(name)
                    log(f"migrate: SKIP {name} (recorded as non-applicable; "
                        f"set ARGUS_RETRY_SKIPPED=1 to retry)")
                else:
                    summary["skipped"].append(name)
                    log(f"migrate: SKIP {name} (already applied, checksum ok)")
                continue
            if prior and prior[0] != digest:
                summary["drift"].append(name)
                log(f"migrate: DRIFT {name}: file changed after being "
                    f"applied (ledger={prior[0][:12]} file={digest[:12]}) - "
                    f"NOT re-applied; add a new migration instead")
                continue
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(open(path, encoding="utf-8").read())
                    _record(conn, name, digest, "applied")
                summary["applied"].append(name)
                log(f"migrate: OK {name}")
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).splitlines()[0][:200]
                if name in SKIP_FILES:
                    try:
                        with conn.transaction():
                            _record(conn, name, digest, "skipped", msg)
                    except Exception:  # noqa: BLE001
                        pass
                    summary["skipped_unsupported"].append(name)
                    log(f"migrate: SKIP {name} (non-fatal, recorded): {msg}")
                else:
                    summary["failed"].append(name)
                    log(f"migrate: FAIL {name}: {msg}")

        # Seed: ledgered like a migration so a changed seed is drift, not silence.
        if os.path.isfile(seed_path):
            name = "seeds/" + os.path.basename(seed_path)
            digest = _checksum(seed_path)
            prior = recorded.get(name)
            if prior and prior[0] == digest:
                log(f"migrate: SKIP {name} (already applied, checksum ok)")
            elif prior and prior[0] != digest:
                summary["drift"].append(name)
                log(f"migrate: DRIFT {name}: changed after being applied - "
                    f"NOT re-applied")
            else:
                try:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(open(seed_path, encoding="utf-8").read())
                        _record(conn, name, digest, "applied")
                    log(f"migrate: OK {name}")
                except Exception as exc:  # noqa: BLE001
                    summary["failed"].append(name)
                    log(f"migrate: FAIL {name}: "
                        f"{str(exc).splitlines()[0][:200]}")

    summary["ok"] = not (summary["failed"] or summary["drift"])
    return summary


def main(argv: list[str] | None = None) -> int:
    """Entrypoint. Exit code is 1 only in explicit fail-fast mode."""
    summary = run_migrations()
    mode = "fatal" if fatal_mode() else "non-fatal"
    if not summary["connected"]:
        msg = summary["error"] or "database unavailable"
        if fatal_mode():
            print(f"migrate: FATAL (MIGRATIONS_FATAL=1): {msg}", flush=True)
            return 1
        print(f"migrate: WARNING non-fatal ({mode}): {msg} - the API will "
              f"start anyway and /health will report the database as "
              f"unreachable", flush=True)
        return 0
    if summary["failed"] or summary["drift"]:
        detail = (f"failed={summary['failed']} drift={summary['drift']} "
                  f"skipped_unsupported={summary['skipped_unsupported']}")
        if fatal_mode():
            print(f"migrate: FATAL ({detail})", flush=True)
            return 1
        print(f"migrate: WARNING non-fatal ({mode}): {detail} - the API will "
              f"start anyway; /health reports the database as reachable but "
              f"the schema may be incomplete", flush=True)
        return 0
    print(f"migrate: done (applied={len(summary['applied'])} "
          f"skipped={len(summary['skipped'])} "
          f"unsupported={len(summary['skipped_unsupported'])})", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

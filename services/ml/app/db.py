"""Postgres layer for the ARGUS ml service (production hardening).

Reads ``DATABASE_URL`` (legacy fallback: ``ARGUS_DB_DSN``). Provides a
process-wide ``psycopg_pool.ConnectionPool`` and schema-conformant helpers
for the migrations in ``db/migrations/001-008``.

Design rules:
  * When no DSN is configured, every helper degrades LOUDLY (one warning)
    and returns None / [] — callers keep their SQLite/file fallbacks.
    Nothing here raises for a missing or unreachable DB; only for
    programmer errors (bad SQL).
  * ``insert_prediction`` is append-only: ``INSERT ... ON CONFLICT
    (as_of, asset_id, horizon, model_version) DO NOTHING``. Corrections are
    new rows, never UPDATE — enforced again at the DB role level
    (007_roles.sql).
  * Mirror tables under the ``svc`` schema (``svc.research_*``,
    ``svc.alert_*``) back the research assistant and alerts routers with
    the same shape as their SQLite sidecars. The production
    ``predictions`` table (004) is used for locked prediction records.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any, Dict, List, Optional

log = logging.getLogger("argus.ml.db")

_pool = None
_warned_unset = False
_warned_unreachable = False
_mirror_ready = False


def database_url() -> Optional[str]:
    """Primary DATABASE_URL, legacy fallback ARGUS_DB_DSN."""
    url = os.environ.get("DATABASE_URL") or os.environ.get("ARGUS_DB_DSN")
    return url or None


def db_configured() -> bool:
    return database_url() is not None


def _loud_unset_warning() -> None:
    global _warned_unset
    if not _warned_unset:
        log.warning(
            "DATABASE_URL is not set — running WITHOUT Postgres. "
            "Predictions will NOT be persisted to the append-only table; "
            "research/alerts use SQLite sidecars. Set DATABASE_URL to enable "
            "production persistence.")
        _warned_unset = True


def get_pool():
    """Process-wide ConnectionPool, or None when no DSN is configured."""
    global _pool
    url = database_url()
    if not url:
        _loud_unset_warning()
        return None
    if _pool is None:
        from psycopg_pool import ConnectionPool
        _pool = ConnectionPool(
            conninfo=url, min_size=1, max_size=10,
            timeout=5, kwargs={"connect_timeout": 5},
        )
        log.info("Postgres connection pool opened (min=1 max=10)")
    return _pool


def close_pool() -> None:
    global _pool, _mirror_ready
    if _pool is not None:
        _pool.close()
        _pool = None
        _mirror_ready = False


def reset_for_tests() -> None:
    """Drop pool + warning flags (tests only)."""
    global _warned_unset, _warned_unreachable
    close_pool()
    _warned_unset = False
    _warned_unreachable = False


def _unreachable_warning(exc: Exception) -> None:
    global _warned_unreachable
    if not _warned_unreachable:
        log.warning("Postgres unreachable (%s) — degrading to local "
                    "fallbacks for this process", exc)
        _warned_unreachable = True


# ---------------------------------------------------------------------------
# Generic helpers (? placeholders are translated to %s)
# ---------------------------------------------------------------------------

def _to_pg_placeholders(sql: str) -> str:
    return sql.replace("?", "%s")


def fetchall(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    pool = get_pool()
    if pool is None:
        return []
    try:
        with pool.connection() as con:
            with con.cursor() as cur:
                cur.execute(_to_pg_placeholders(sql), params)
                cols = [d[0] for d in cur.description] if cur.description else []
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001 — DB must never break serving
        _unreachable_warning(exc)
        return []


def fetchone(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    rows = fetchall(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: tuple = ()) -> int:
    """Run a write; returns rowcount, 0 when the DB is unavailable."""
    pool = get_pool()
    if pool is None:
        return 0
    try:
        with pool.connection() as con:
            with con.cursor() as cur:
                cur.execute(_to_pg_placeholders(sql), params)
                n = cur.rowcount
            con.commit()
            return n
    except Exception as exc:  # noqa: BLE001
        _unreachable_warning(exc)
        return 0


# ---------------------------------------------------------------------------
# Mirror tables for the research/alerts SQLite sidecars (svc schema)
# ---------------------------------------------------------------------------

MIRROR_DDL = """
CREATE SCHEMA IF NOT EXISTS svc;
CREATE TABLE IF NOT EXISTS svc.research_assets (
    id TEXT PRIMARY KEY, ticker TEXT NOT NULL UNIQUE, name TEXT,
    asset_type TEXT, sector TEXT, industry TEXT, sp500 INTEGER DEFAULT 0,
    pe_ratio REAL, data_status TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE');
CREATE TABLE IF NOT EXISTS svc.research_predictions (
    id TEXT PRIMARY KEY, as_of TEXT NOT NULL, asset_id TEXT NOT NULL,
    horizon INTEGER NOT NULL, model_version TEXT NOT NULL, regime TEXT,
    p_positive REAL, p_negative REAL, expected_return REAL, expected_vol REAL,
    expected_high REAL, expected_low REAL, ci_lower REAL, ci_upper REAL,
    downside_risk REAL, upside_potential REAL, risk_reward REAL,
    composite_score REAL, trend_strength REAL, momentum_score REAL,
    fundamental_score REAL, risk_score REAL, volatility_score REAL,
    confidence REAL, engine_signals TEXT, explanation TEXT,
    data_status_overall TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE',
    UNIQUE (as_of, asset_id, horizon, model_version));
CREATE TABLE IF NOT EXISTS svc.research_prediction_outcomes (
    id TEXT PRIMARY KEY, prediction_id TEXT NOT NULL UNIQUE,
    observed_at TEXT NOT NULL, actual_return REAL,
    direction_correct INTEGER, data_status TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE');
CREATE TABLE IF NOT EXISTS svc.research_backtests (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, config TEXT, universe TEXT,
    start_date TEXT, end_date TEXT, created_at TEXT, cagr REAL, sharpe REAL,
    max_drawdown REAL, win_rate REAL, n_trades INTEGER,
    data_status TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE');
CREATE TABLE IF NOT EXISTS svc.research_model_metrics_daily (
    day TEXT NOT NULL, model_version TEXT NOT NULL, horizon INTEGER NOT NULL,
    regime TEXT, sector TEXT, n INTEGER, accuracy REAL, precision REAL,
    recall REAL, f1 REAL, roc_auc REAL, brier REAL, log_loss REAL,
    cal_error REAL, mae REAL, rmse REAL, directional_accuracy REAL,
    data_status TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE',
    PRIMARY KEY (day, model_version, horizon, regime, sector));
CREATE TABLE IF NOT EXISTS svc.alert_rules (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT 'local',
    rule_type TEXT NOT NULL, asset_id TEXT, ticker TEXT,
    params TEXT NOT NULL DEFAULT '{}', is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS svc.alerts (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL, alert_type TEXT NOT NULL, asset_id TEXT,
    ticker TEXT, title TEXT NOT NULL, body TEXT,
    severity TEXT NOT NULL DEFAULT 'INFO', channel TEXT NOT NULL DEFAULT 'IN_APP',
    delivered_at TEXT, read_at TEXT, rule_id TEXT);
"""


def ensure_mirror() -> bool:
    """Create svc mirror tables (idempotent, best-effort)."""
    global _mirror_ready
    if _mirror_ready or not db_configured():
        return _mirror_ready
    pool = get_pool()
    if pool is None:
        return False
    try:
        with pool.connection() as con:
            with con.cursor() as cur:
                # The append-only app role has no CREATE privilege on the
                # database, so CREATE SCHEMA would raise InsufficientPrivilege
                # even when the schema already exists. The schema is created
                # once by the deploy (migrations/ops); skip that statement
                # when it is already there.
                cur.execute(
                    "SELECT 1 FROM information_schema.schemata "
                    "WHERE schema_name = 'svc'")
                ddl = MIRROR_DDL
                if cur.fetchone():
                    ddl = ";\n".join(
                        s for s in MIRROR_DDL.split(";\n")
                        if not s.strip().upper().startswith("CREATE SCHEMA"))
                cur.execute(ddl)
            con.commit()
        _mirror_ready = True
        log.info("svc mirror tables ensured")
        return True
    except Exception as exc:  # noqa: BLE001
        _unreachable_warning(exc)
        return False


# ---------------------------------------------------------------------------
# Production tables (migrations 001-008 schema)
# ---------------------------------------------------------------------------

def ensure_asset(ticker: str, name: Optional[str] = None,
                 asset_type: str = "STOCK",
                 sector: Optional[str] = None) -> Optional[str]:
    """Return the assets.id for ``ticker``, creating the row if needed."""
    t = ticker.upper()
    row = fetchone("SELECT id FROM assets WHERE ticker = ?", (t,))
    if row:
        return str(row["id"])
    new_id = str(uuid.uuid4())
    n = execute(
        "INSERT INTO assets (id, ticker, name, asset_type, sector, is_active)"
        " VALUES (?, ?, ?, ?, ?, TRUE)"
        " ON CONFLICT (ticker) DO NOTHING",
        (new_id, t, name or t, asset_type, sector))
    if n == 0 and not db_configured():
        return None
    row = fetchone("SELECT id FROM assets WHERE ticker = ?", (t,))
    return str(row["id"]) if row else None


def insert_prediction(payload: Dict[str, Any]) -> Optional[str]:
    """Persist a locked prediction record (append-only).

    ``payload`` is the dict from ``app.predict.build_live_prediction``
    (before ``_debug`` is stripped — it is ignored here). Returns the row
    id, or None when the DB is unavailable. Never raises.
    """
    if not db_configured():
        _loud_unset_warning()
        return None
    try:
        asset_id = ensure_asset(
            payload["ticker"], sector=payload.get("sector"))
        if not asset_id:
            return None
        ci = payload.get("ci") or [None, None]
        explanation = payload.get("explanation") or {}
        import json as _json
        row = fetchone(
            """INSERT INTO predictions (
                 as_of, asset_id, horizon, model_version, regime,
                 p_positive, p_negative, expected_return, expected_vol,
                 expected_high, expected_low, ci_lower, ci_upper,
                 downside_risk, upside_potential, risk_reward,
                 composite_score, confidence,
                 engine_signals, explanation, invalidation,
                 data_status_overall)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?::jsonb, ?::jsonb, ?::jsonb, ?)
               ON CONFLICT (as_of, asset_id, horizon, model_version)
               DO NOTHING
               RETURNING id""",
            (payload.get("as_of"), asset_id, payload.get("horizon"),
             payload.get("model_version"), payload.get("regime"),
             payload.get("p_positive"), payload.get("p_negative"),
             payload.get("expected_return"), payload.get("expected_vol"),
             payload.get("expected_high"), payload.get("expected_low"),
             ci[0], ci[1],
             payload.get("downside_risk"), payload.get("upside_potential"),
             payload.get("risk_reward"),
             payload.get("composite_score"), payload.get("confidence"),
             _json.dumps(payload.get("engine_signals") or {}),
             _json.dumps(explanation),
             _json.dumps(explanation.get("invalidated_if") or []),
             payload.get("data_status", "UNCONFIRMED")))
        if row:
            log.info("persisted prediction %s %s h=%s (%s)",
                     payload["ticker"], payload.get("as_of"),
                     payload.get("horizon"), row["id"])
            return str(row["id"])
        return None  # conflict → already locked; correct, not an error
    except Exception as exc:  # noqa: BLE001 — persistence never breaks serving
        log.warning("insert_prediction failed (serving continues): %s", exc)
        return None


def get_latest_prediction(ticker: str, horizon: int) -> Optional[Dict[str, Any]]:
    """Newest locked record for (ticker, horizon) across model versions."""
    return fetchone(
        """SELECT p.*, a.ticker FROM predictions p
           JOIN assets a ON a.id = p.asset_id
           WHERE a.ticker = ? AND p.horizon = ?
           ORDER BY p.as_of DESC LIMIT 1""",
        (ticker.upper(), horizon))

"""Run ARGUS SQL migrations + calibration seed on container startup.

Idempotent: every migration uses IF NOT EXISTS / DO guards, so re-running
on each deploy/restart is a safe no-op. 007_roles.sql needs a superuser
(CREATE ROLE) and is skipped on managed Postgres; the app then uses the
service's admin role directly.
"""
import glob
import os
import sys

import psycopg

DB_URL = os.environ.get("DATABASE_URL")
if not DB_URL:
    print("migrate: DATABASE_URL not set — skipping migrations", flush=True)
    sys.exit(0)

BASE_CANDIDATES = [
    "/srv/argus/db",  # container layout
    os.path.join(  # repo layout: <repo>/db
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))),
        "db",
    ),
]
BASE = next((b for b in BASE_CANDIDATES if os.path.isdir(b)), BASE_CANDIDATES[0])
MIG_DIR = os.path.join(BASE, "migrations")
SEED = os.path.join(BASE, "seeds", "calibration_maps_seed.sql")

SKIP = {"007_roles.sql"}  # needs superuser; not available on managed DBs

files = sorted(glob.glob(os.path.join(MIG_DIR, "*.sql")))
if not files:
    print(f"migrate: no migration files in {MIG_DIR} — skipping", flush=True)
    sys.exit(0)

print(f"migrate: running {len(files)} migrations", flush=True)
failed = []
with psycopg.connect(DB_URL, autocommit=True) as conn:
    for f in files:
        name = os.path.basename(f)
        try:
            with conn.cursor() as cur:
                cur.execute(open(f).read())
            print(f"migrate: OK {name}", flush=True)
        except Exception as e:
            msg = str(e).split("\n")[0][:160]
            if name in SKIP:
                print(f"migrate: SKIP {name} (non-fatal): {msg}", flush=True)
                conn.rollback()
            else:
                print(f"migrate: FAIL {name}: {msg}", flush=True)
                failed.append(name)
                conn.rollback()
    if os.path.isfile(SEED):
        try:
            with conn.cursor() as cur:
                cur.execute(open(SEED).read())
            print("migrate: OK calibration seed", flush=True)
        except Exception as e:
            print(f"migrate: FAIL seed: {str(e).splitlines()[0][:160]}", flush=True)
            failed.append("seed")
            conn.rollback()

if failed:
    print(f"migrate: FAILED: {failed}", flush=True)
    sys.exit(1)
print("migrate: done", flush=True)

-- =============================================================================
-- ARGUS migration 001 — extensions and schemas
-- Idempotent: safe to re-run (CREATE ... IF NOT EXISTS).
-- Run order: 001 before everything (it defines argus_maybe_hypertable(),
-- used by the create_hypertable() call sites in 003/004/005).
-- =============================================================================

-- gen_random_uuid() for UUID primary keys
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- TimescaleDB for hypertables (time-series tables) — OPTIONAL.
-- Installed only when the extension is available on this server.
-- On stock Postgres without TimescaleDB (e.g. Neon free tier) this is a
-- no-op: time-series tables stay plain tables with supporting indexes,
-- via argus_maybe_hypertable() below. Must NOT fail on stock Postgres.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb') THEN
        CREATE EXTENSION IF NOT EXISTS timescaledb;
    ELSE
        RAISE NOTICE 'timescaledb extension not available on this server — running in plain-Postgres mode (no hypertables)';
    END IF;
END
$$;

-- Conditional hypertable helper used by 003/004/005.
-- If the timescaledb extension is installed in this database, converts the
-- table to a hypertable (idempotent via if_not_exists). Otherwise does
-- nothing: the table stays a plain table and the supporting indexes each
-- migration already creates (CREATE INDEX IF NOT EXISTS) carry the queries.
-- Safe to call on stock Postgres: the create_hypertable() reference is only
-- resolved at execution time, inside the branch that requires the extension.
CREATE OR REPLACE FUNCTION argus_maybe_hypertable(tbl TEXT, time_col TEXT, chunk_us BIGINT)
RETURNS void
LANGUAGE plpgsql
AS $func$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        PERFORM create_hypertable(tbl, time_col,
            chunk_time_interval => chunk_us,
            if_not_exists => TRUE);
    ELSE
        RAISE NOTICE 'timescaledb not installed in this database — % remains a plain table', tbl;
    END IF;
END
$func$;

COMMENT ON FUNCTION argus_maybe_hypertable(TEXT, TEXT, BIGINT) IS
  'Migration helper: create_hypertable() when TimescaleDB is installed, '
  'otherwise leave the table as a plain table (supporting indexes are '
  'created separately by each migration). Idempotent and safe on stock Postgres.';

-- Research schema: physically separate store for backtests / research.
-- Backtests must never contaminate production records in public.*
CREATE SCHEMA IF NOT EXISTS research;

COMMENT ON SCHEMA research IS
  'Isolated research store: backtests, backtest trades and metrics. '
  'Separate from public.* so research never contaminates live prediction records.';

-- =============================================================================
-- ARGUS migration 009 — schema hardening (svc boot, indexes, RLS, immutability)
--
-- Companion to services/ml/app/migrate.py (ledger + checksums) and the boot
-- restructure: a database outage can no longer keep /health from answering.
-- Everything here is idempotent and safe to re-run; every statement that
-- depends on an object that may not exist yet is guarded.
--
-- CONTENTS
--   1. svc schema + mirror tables (previously created ad hoc at runtime by
--      app/db.py:ensure_mirror) so grants/indexes/RLS are deterministic.
--   2. Missing indexes on the svc mirror tables the API queries.
--   3. Row Level Security (ENABLE + FORCE + policies) on the user-owned
--      public tables (portfolios, alerts, alert_rules).
--   4. Immutability triggers on the append-only tables (predictions,
--      prediction_outcomes) — 007's REVOKEs are inert when the app connects
--      as the admin role, so the guard must live on the table.
--   5. predictions.asset_id: ON DELETE CASCADE -> RESTRICT (the append-only
--      policy says a locked prediction is never deleted as a side effect).
--   6. Grants for the svc schema + default privileges for future svc objects.
--   7. DOCUMENTED NOT-APPLIED items (svc RLS, predictions.model_version FK,
--      features horizon PK) with the exact DDL and the reason.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- 1. svc schema + mirror tables
--    Source of truth for the columns: services/ml/app/db.py MIRROR_DDL
--    (kept in sync with the SQLite sidecar shape in app/routers/alerts.py).
--    Future column additions must use ALTER TABLE ... ADD COLUMN IF NOT EXISTS
--    in a NEW migration so this one stays immutable in the ledger.
-- ---------------------------------------------------------------------------
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

COMMENT ON TABLE svc.alerts IS
  'Alert instances written by app/routers/alerts.py when DATABASE_URL is set.';
COMMENT ON TABLE svc.alert_rules IS
  'Alert rules written by app/routers/alerts.py when DATABASE_URL is set.';

-- ---------------------------------------------------------------------------
-- 2. Indexes: svc.alerts / svc.alert_rules had NO indexes at all.
--    (The public.alerts / public.alert_rules tables from 005 already carry
--    idx_alerts_user, idx_alerts_created, idx_alert_rules_user and friends.)
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_svc_alerts_user_created
    ON svc.alerts (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_svc_alerts_ticker
    ON svc.alerts (ticker, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_svc_alerts_unread
    ON svc.alerts (user_id) WHERE read_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_svc_alert_rules_user_active
    ON svc.alert_rules (user_id, is_active);
CREATE INDEX IF NOT EXISTS idx_svc_alert_rules_ticker
    ON svc.alert_rules (ticker);
CREATE INDEX IF NOT EXISTS idx_svc_research_preds_asof
    ON svc.research_predictions (as_of DESC, asset_id);
CREATE INDEX IF NOT EXISTS idx_svc_research_preds_asset
    ON svc.research_predictions (asset_id, horizon);
CREATE INDEX IF NOT EXISTS idx_svc_research_bt_created
    ON svc.research_backtests (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_svc_research_metrics_day
    ON svc.research_model_metrics_daily (day DESC, model_version);

-- ---------------------------------------------------------------------------
-- 3. Row Level Security for the user-owned public tables.
--
--    Owner column: user_id UUID NOT NULL REFERENCES users(id). The caller
--    identity is taken from the per-transaction GUC `argus.user_id`, which
--    the API must set with, e.g.:
--        SELECT set_config('argus.user_id', $1, true);
--    When it is unset the helper returns NULL and the policy matches NOTHING:
--    fail-closed by construction.
--
--    FORCE ROW LEVEL SECURITY means the table OWNER (the admin role the app
--    uses) is also subject to the policies. That is deliberate: without FORCE
--    every policy would be inert for the role that actually connects.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION argus_current_user_id() RETURNS uuid
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v text;
BEGIN
    v := current_setting('argus.user_id', true);
    IF v IS NULL OR btrim(v) = '' THEN
        RETURN NULL;
    END IF;
    BEGIN
        RETURN v::uuid;
    EXCEPTION WHEN others THEN
        RETURN NULL;
    END;
END
$$;

COMMENT ON FUNCTION argus_current_user_id() IS
  'Tenant identity for RLS policies, read from the argus.user_id GUC. '
  'Returns NULL (=> no rows visible, inserts rejected) when unset.';

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['portfolios', 'alerts', 'alert_rules'] LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I',
                       t || '_owner_isolation', t);
        EXECUTE format(
            'CREATE POLICY %I ON public.%I '
            'USING (user_id = argus_current_user_id()) '
            'WITH CHECK (user_id = argus_current_user_id())',
            t || '_owner_isolation', t);
    END LOOP;
END
$$;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['portfolios', 'alerts', 'alert_rules'] LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format(
            'COMMENT ON POLICY %I ON public.%I IS %L',
            t || '_owner_isolation', t,
            'Tenant isolation: a session sees only its own rows (argus.user_id).');
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- 4. Immutability triggers.
--    ARCHITECTURE.md §1: a locked prediction is append-only; corrections are
--    NEW ROWS. 007 revokes UPDATE/DELETE from argus_app, but that is inert
--    when the app connects as the table owner/admin, so enforce it on the
--    table. app/db.py writes predictions with INSERT ... ON CONFLICT DO
--    NOTHING (no UPDATE), so this cannot break the writer.
--    DOCUMENTED LIMITATION: this is a row-level trigger, so TRUNCATE is not
--    covered; revoke TRUNCATE from the application role for that.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION argus_forbid_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'ARGUS: %.% is append-only; % is not permitted (insert a new row)',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$;

COMMENT ON FUNCTION argus_forbid_mutation() IS
  'BEFORE UPDATE/DELETE guard for append-only tables (predictions, '
  'prediction_outcomes). Raises restrict_violation.';

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['predictions', 'prediction_outcomes'] LOOP
        IF to_regclass('public.' || t) IS NULL THEN
            CONTINUE;
        END IF;
        EXECUTE format('DROP TRIGGER IF EXISTS %I ON public.%I',
                       'trg_' || t || '_append_only', t);
        EXECUTE format(
            'CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON public.%I '
            'FOR EACH ROW EXECUTE FUNCTION argus_forbid_mutation()',
            'trg_' || t || '_append_only', t);
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- 5. predictions.asset_id: ON DELETE CASCADE -> RESTRICT.
--    Deleting an asset must not silently delete locked predictions. Only the
--    FK that is actually CASCADE is rewritten, so re-running is a no-op.
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    cname text;
BEGIN
    IF to_regclass('public.predictions') IS NULL
       OR to_regclass('public.assets') IS NULL THEN
        RETURN;
    END IF;
    FOR cname IN
        SELECT con.conname
        FROM pg_constraint con
        JOIN pg_class rel ON rel.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = rel.relnamespace
        JOIN pg_attribute att
             ON att.attrelid = rel.oid AND att.attnum = ANY (con.conkey)
        WHERE con.contype = 'f'
          AND ns.nspname = 'public' AND rel.relname = 'predictions'
          AND att.attname = 'asset_id'
          AND con.confdeltype = 'c'          -- 'c' = ON DELETE CASCADE
    LOOP
        EXECUTE format('ALTER TABLE public.predictions DROP CONSTRAINT %I',
                       cname);
        EXECUTE 'ALTER TABLE public.predictions '
                'ADD CONSTRAINT predictions_asset_id_restrict_fkey '
                'FOREIGN KEY (asset_id) REFERENCES public.assets (id) '
                'ON DELETE RESTRICT';
        RAISE NOTICE 'ARGUS 009: predictions.asset_id FK % -> ON DELETE RESTRICT',
                     cname;
    END LOOP;
END
$$;

-- ---------------------------------------------------------------------------
-- 6. svc grants (Part 1C: 007 grants nothing on svc and is skipped on managed
--    Postgres). The service role is created by 007 when a superuser exists;
--    when it does not exist the grants are a documented no-op and the
--    migration is still deterministic. Default privileges cover the objects
--    the app creates later as the same role.
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'argus_app') THEN
        EXECUTE 'GRANT USAGE ON SCHEMA svc TO argus_app';
        EXECUTE 'GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA svc TO argus_app';
        EXECUTE 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA svc TO argus_app';
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA svc '
            'GRANT SELECT, INSERT ON TABLES TO argus_app', current_user);
        EXECUTE format(
            'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA svc '
            'GRANT USAGE, SELECT ON SEQUENCES TO argus_app', current_user);
        RAISE NOTICE 'ARGUS 009: svc grants applied for argus_app';
    ELSE
        RAISE NOTICE 'ARGUS 009: role argus_app absent (007 skipped on managed '
                     'Postgres); svc grants not applicable - the API connects '
                     'with the admin role';
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- 7. DOCUMENTED NOT-APPLIED (deliberate; see changes/D-db-boot-quant.md §SQL)
--
-- (a) RLS on svc.alerts / svc.alert_rules: NOT enabled. Those tables are
--     written by app/routers/alerts.py with user_id hardcoded to the literal
--     'local' (no per-request identity), and the API does not set
--     argus.user_id. Enabling fail-closed RLS here would make every alert
--     read return zero rows and every alert insert fail, i.e. a self-inflicted
--     outage with no isolation gain (all rows share one owner). Prerequisite:
--     routers must derive the subject from the verified token and set
--     argus.user_id per transaction; then apply:
--       -- ALTER TABLE svc.alert_rules ENABLE ROW LEVEL SECURITY;
--       -- ALTER TABLE svc.alert_rules FORCE ROW LEVEL SECURITY;
--       -- CREATE POLICY svc_alert_rules_owner ON svc.alert_rules
--       --     USING (user_id = argus_current_svc_user())
--       --     WITH CHECK (user_id = argus_current_svc_user());
--     (with argus_current_svc_user() returning current_setting('argus.user_id', true)
--      and NOT defaulting to 'local'.) svc.research_* mirror catalog/research
--     data with no owner column at all, so RLS does not apply.
--
-- (b) predictions.model_version foreign key: NOT added. engine_weights and
--     calibration_maps do have one, but model_version is populated from the
--     FILE registry (services/ml/app/registry/*.json: ARGUS-EQ-1.0 and
--     ARGUS-EQ-1.1), while seeds/calibration_maps_seed.sql only inserts
--     ARGUS-EQ-1.1. An enforced FK would reject valid locked predictions for
--     any registry version not yet inserted into model_versions, converting a
--     working append-only write into a failure. The index
--     idx_predictions_version already exists (004) so the join is not the
--     bottleneck. Revisit when model_versions is written from the registry on
--     every deploy.
--
-- (c) features primary key: (as_of, asset_id, engine, feature_name) omits
--     horizon, so two rows for the same feature at different horizons at the
--     same as_of collide. Verified: no code in services/ml inserts into
--     features (only assets, predictions and system_logs are written), so the
--     collision is not reachable today. A correct fix requires rebuilding the
--     table on its TimescaleDB hypertable (horizon is NULLable, and NULL
--     cannot participate in a PRIMARY KEY):
--       -- ALTER TABLE features ADD COLUMN horizon_default INT
--       --     GENERATED ALWAYS AS (COALESCE(horizon, -1)) STORED;  -- or
--       -- ALTER TABLE features DROP CONSTRAINT features_pkey;
--       -- ALTER TABLE features ALTER COLUMN horizon SET NOT NULL;
--       -- ALTER TABLE features ADD PRIMARY KEY
--       --     (as_of, asset_id, engine, feature_name, horizon);
--     Left as a deliberate, separately-reviewed change rather than an
--     unverified table rebuild inside an idempotent migration.
-- ---------------------------------------------------------------------------

DO $$
BEGIN
    RAISE NOTICE 'ARGUS 009: schema hardening applied (svc boot+indexes, RLS '
                 'on user-owned public tables, append-only triggers, '
                 'predictions FK RESTRICT)';
END
$$;

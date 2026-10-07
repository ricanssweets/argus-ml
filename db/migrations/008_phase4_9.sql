-- 008_phase4_9.sql — Phases 4-9 integration checkpoint.
--
-- Verified 2026-10-06: Phases 4-9 require NO schema changes.
--   * Engines C/D/F/G read from existing tables (fundamentals, macro_data,
--     options_chains, news, sentiment) via application connectors; no new
--     columns needed.
--   * Regime history uses the existing market_regimes table shape.
--   * Research assistant + alerts use application-level SQLite sidecars
--     (services/ml/data/research_store.db, services/ml/data/alerts.db)
--     mirroring the migration table names; promotion to Postgres tables
--     (predictions, research.backtests, model_metrics_daily, alert_rules,
--     alerts) needs no DDL.
--   * Observability writes degradation_flags (004) as-is; promotion
--     decisions are human-gated and recorded in
--     data/promotion_decisions.jsonl, never auto-applied to
--     model_versions.status.
--   * New DataStatus literal 'SYNTHETIC_FIXTURE' is application-level only.
--
-- This migration is intentionally a no-op marker so the sequence stays
-- explicit and future schema work continues at 009.

DO $$
BEGIN
    RAISE NOTICE 'ARGUS 008: no DDL required for Phases 4-9 (verified 2026-10-06)';
END
$$;

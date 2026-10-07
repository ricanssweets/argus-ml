-- =============================================================================
-- ARGUS migration 007 — application role and immutability policy
-- Idempotent: role creation guarded by a DO block on pg_roles; GRANT/REVOKE
-- are naturally idempotent (re-running is a no-op, revoking a never-granted
-- privilege only emits a WARNING).
--
-- Immutability policy (ARCHITECTURE.md §1, rule 2):
--   A locked prediction is immutable. Once generated for an as-of timestamp
--   it is append-only: a new model version never rewrites history.
--   Corrections are NEW ROWS in public.predictions, never UPDATE/DELETE.
--   The argus_app role therefore holds SELECT + INSERT only on
--   public.predictions (UPDATE/DELETE explicitly revoked below), and the
--   same holds for public.prediction_outcomes, which is written exactly
--   once via INSERT when a horizon matures and never touched afterwards.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Role (NOLOGIN service role)
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'argus_app') THEN
        CREATE ROLE argus_app NOLOGIN;
    END IF;
END
$$;

-- ---------------------------------------------------------------------------
-- Database + schema access
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    EXECUTE format('GRANT CONNECT ON DATABASE %I TO argus_app', current_database());
END
$$;

GRANT USAGE ON SCHEMA public  TO argus_app;
GRANT USAGE ON SCHEMA research TO argus_app;

-- ---------------------------------------------------------------------------
-- Table privileges: SELECT + INSERT everywhere the app writes.
-- UPDATE/DELETE are never granted; the explicit REVOKEs below are
-- belt-and-suspenders against any future default-privilege change.
-- ---------------------------------------------------------------------------
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public   TO argus_app;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA research TO argus_app;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public   TO argus_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA research TO argus_app;

-- Predictions: append-only. No UPDATE, no DELETE — ever.
REVOKE UPDATE, DELETE ON public.predictions FROM argus_app;

-- Outcomes: written once via INSERT when the horizon matures, never touched.
-- Kept at INSERT + SELECT only; no UPDATE/DELETE granted or allowed.
REVOKE UPDATE, DELETE ON public.prediction_outcomes FROM argus_app;

-- Future tables created by the migration role inherit the same posture.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT SELECT, INSERT ON TABLES TO argus_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA research
    GRANT SELECT, INSERT ON TABLES TO argus_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO argus_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA research
    GRANT USAGE, SELECT ON SEQUENCES TO argus_app;

-- ---------------------------------------------------------------------------
-- Documented immutability policy on the tables themselves
-- ---------------------------------------------------------------------------
COMMENT ON TABLE public.predictions IS
  'Locked prediction records — APPEND-ONLY AND IMMUTABLE BY POLICY. '
  'One row per (as_of, asset_id, horizon, model_version). The argus_app role '
  'holds SELECT + INSERT only (UPDATE/DELETE explicitly revoked); a new model '
  'version or a correction is always a NEW ROW, never an edit of history.';

COMMENT ON TABLE public.prediction_outcomes IS
  'Observed outcomes for locked predictions. Written exactly once via INSERT '
  'when a horizon matures; never updated afterwards (argus_app holds '
  'INSERT + SELECT only, UPDATE/DELETE explicitly revoked).';

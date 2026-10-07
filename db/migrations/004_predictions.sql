-- =============================================================================
-- ARGUS migration 004 — feature store, predictions (append-only),
-- model registry & observatory.
-- Hypertables when TimescaleDB is installed (plain tables with supporting
-- indexes otherwise): features (on as_of), model_metrics_daily (on day).
-- predictions is APPEND-ONLY: one locked record per
-- (as_of, asset_id, horizon, model_version). Corrections are new rows,
-- never edits — enforced for the app role in 007_roles.sql.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- models — registry of model families
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS models (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name           TEXT        NOT NULL,
    family         TEXT        NOT NULL CHECK (family IN ('XGB', 'LGBM', 'RF', 'ELASTICNET', 'LOGRREG', 'SVM', 'MLP', 'LSTM', 'TCN', 'TRANSFORMER', 'STACKER')),
    target_horizon INT         NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- model_versions — champion/challenger registry
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_versions (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    model_id        UUID        NOT NULL REFERENCES models (id) ON DELETE CASCADE,
    version         TEXT        NOT NULL UNIQUE,
    status          TEXT        NOT NULL CHECK (status IN ('EXPERIMENT', 'STAGING', 'CHALLENGER', 'CHAMPION', 'RETIRED')),
    trained_on_range TSTZRANGE,
    data_version    INT,
    hyperparams     JSONB,
    promoted_at     TIMESTAMPTZ,
    promotion_basis TEXT,
    metrics         JSONB
);

CREATE INDEX IF NOT EXISTS idx_model_versions_model  ON model_versions (model_id);
CREATE INDEX IF NOT EXISTS idx_model_versions_status ON model_versions (status);

-- ---------------------------------------------------------------------------
-- features — point-in-time feature store. Hypertable on as_of when TimescaleDB
-- is installed (plain table with supporting indexes otherwise).
-- Anti-leakage invariant: a feature for as_of may only read inputs <= as_of.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS features (
    as_of        TIMESTAMPTZ    NOT NULL,
    asset_id     UUID           NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    horizon      INT,
    engine       CHAR(1)        NOT NULL CHECK (engine IN ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H')),
    feature_name TEXT           NOT NULL,
    feature_value DOUBLE PRECISION,
    data_status  TEXT           NOT NULL DEFAULT 'UNCONFIRMED'
                     CHECK (data_status IN ('CONFIRMED', 'LIKELY', 'UNCONFIRMED', 'CONFLICTING', 'MISSING')),
    PRIMARY KEY (as_of, asset_id, engine, feature_name)
);

-- Hypertable when TimescaleDB is installed, plain table + supporting index otherwise.
SELECT argus_maybe_hypertable('features', 'as_of', 7 * 86400000000);  -- 7 days in microseconds

CREATE INDEX IF NOT EXISTS idx_features_asset_asof ON features (asset_id, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_features_asof_brin  ON features USING BRIN (as_of);

-- ---------------------------------------------------------------------------
-- predictions — the locked, immutable record. Append-only.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS predictions (
    id                 UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_of              TIMESTAMPTZ NOT NULL,
    asset_id           UUID        NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    horizon            INT         NOT NULL,
    model_version      TEXT        NOT NULL,
    regime             TEXT,
    p_positive         DOUBLE PRECISION,
    p_negative         DOUBLE PRECISION,
    expected_return    DOUBLE PRECISION,
    expected_vol       DOUBLE PRECISION,
    expected_high      DOUBLE PRECISION,
    expected_low       DOUBLE PRECISION,
    ci_lower           DOUBLE PRECISION,
    ci_upper           DOUBLE PRECISION,
    downside_risk      DOUBLE PRECISION,
    upside_potential   DOUBLE PRECISION,
    risk_reward        DOUBLE PRECISION,
    composite_score    DOUBLE PRECISION,
    trend_strength     DOUBLE PRECISION,
    momentum_score     DOUBLE PRECISION,
    fundamental_score  DOUBLE PRECISION,
    risk_score         DOUBLE PRECISION,
    volatility_score   DOUBLE PRECISION,
    confidence         DOUBLE PRECISION,
    engine_signals     JSONB,
    explanation        JSONB,
    invalidation       JSONB,
    feature_hash       TEXT,
    data_version       INT,
    data_status_overall TEXT       NOT NULL DEFAULT 'UNCONFIRMED'
                        CHECK (data_status_overall IN ('CONFIRMED', 'LIKELY', 'UNCONFIRMED', 'CONFLICTING', 'MISSING')),
    UNIQUE (as_of, asset_id, horizon, model_version)
);

CREATE INDEX IF NOT EXISTS idx_predictions_asset    ON predictions (asset_id);
CREATE INDEX IF NOT EXISTS idx_predictions_asset_asof ON predictions (asset_id, as_of DESC);
CREATE INDEX IF NOT EXISTS idx_predictions_version  ON predictions (model_version);

-- ---------------------------------------------------------------------------
-- prediction_outcomes — written once when the horizon matures, then untouched.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prediction_outcomes (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_id    UUID        NOT NULL UNIQUE REFERENCES predictions (id) ON DELETE CASCADE,
    observed_at      TIMESTAMPTZ NOT NULL,
    actual_return    DOUBLE PRECISION,
    actual_high      DOUBLE PRECISION,
    actual_low       DOUBLE PRECISION,
    direction_correct BOOLEAN,
    brier_contrib    DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_outcomes_prediction ON prediction_outcomes (prediction_id);

-- ---------------------------------------------------------------------------
-- engine_weights — learned ensemble weights, pinned per model version
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS engine_weights (
    model_version TEXT    NOT NULL REFERENCES model_versions (version) ON DELETE CASCADE,
    regime        TEXT    NOT NULL,
    engine        CHAR(1) NOT NULL CHECK (engine IN ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H')),
    horizon       INT     NOT NULL,
    weight        DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (model_version, regime, engine, horizon)
);

CREATE INDEX IF NOT EXISTS idx_engine_weights_version ON engine_weights (model_version);

-- ---------------------------------------------------------------------------
-- calibration_maps — one isotonic/Platt map per (model_version, horizon).
-- min_samples >= 20 enforced (MIN_SAMPLES_PER_BUCKET rule).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS calibration_maps (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version TEXT        NOT NULL REFERENCES model_versions (version) ON DELETE CASCADE,
    horizon       INT         NOT NULL,
    method        TEXT        NOT NULL CHECK (method IN ('ISOTONIC', 'PLATT')),
    buckets       JSONB       NOT NULL,
    fitted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    min_samples   INT         NOT NULL CHECK (min_samples >= 20),
    UNIQUE (model_version, horizon)
);

CREATE INDEX IF NOT EXISTS idx_calibration_version ON calibration_maps (model_version);

-- ---------------------------------------------------------------------------
-- training_runs — reproducibility audit trail
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS training_runs (
    id                    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version         TEXT        NOT NULL,
    started_at            TIMESTAMPTZ NOT NULL,
    finished_at           TIMESTAMPTZ,
    config                JSONB,
    metrics               JSONB,
    leakage_checks_passed BOOLEAN,
    seed                  INT,
    git_sha               TEXT
);

CREATE INDEX IF NOT EXISTS idx_training_runs_version ON training_runs (model_version);

-- ---------------------------------------------------------------------------
-- model_metrics_daily — observatory time series. Hypertable on day when
-- TimescaleDB is installed (plain table with supporting indexes otherwise).
-- Powers degradation detection (rolling 63-day windows).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_metrics_daily (
    day                  TIMESTAMPTZ NOT NULL,
    model_version        TEXT        NOT NULL,
    horizon              INT         NOT NULL,
    regime               TEXT,
    sector               TEXT,
    n                    INT,
    accuracy             DOUBLE PRECISION,
    precision            DOUBLE PRECISION,
    recall               DOUBLE PRECISION,
    f1                   DOUBLE PRECISION,
    roc_auc              DOUBLE PRECISION,
    brier                DOUBLE PRECISION,
    log_loss             DOUBLE PRECISION,
    cal_error            DOUBLE PRECISION,
    mae                  DOUBLE PRECISION,
    rmse                 DOUBLE PRECISION,
    directional_accuracy DOUBLE PRECISION
);

-- Hypertable when TimescaleDB is installed, plain table + supporting index otherwise.
SELECT argus_maybe_hypertable('model_metrics_daily', 'day', 7 * 86400000000);  -- 7 days in microseconds

CREATE INDEX IF NOT EXISTS idx_metrics_version_day ON model_metrics_daily (model_version, day DESC);
CREATE INDEX IF NOT EXISTS idx_metrics_day         ON model_metrics_daily (day DESC);

-- ---------------------------------------------------------------------------
-- degradation_flags — WATCH / WARN / CRITICAL events from the nightly job
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS degradation_flags (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    detected_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    model_version TEXT        NOT NULL,
    horizon       INT         NOT NULL,
    metric        TEXT        NOT NULL,
    baseline      DOUBLE PRECISION,
    current       DOUBLE PRECISION,
    window_days   INT         NOT NULL,
    severity      TEXT        NOT NULL CHECK (severity IN ('WATCH', 'WARN', 'CRITICAL')),
    acknowledged  BOOLEAN     NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_degradation_version ON degradation_flags (model_version);
CREATE INDEX IF NOT EXISTS idx_degradation_detected ON degradation_flags (detected_at DESC);

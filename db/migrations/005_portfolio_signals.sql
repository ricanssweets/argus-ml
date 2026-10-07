-- =============================================================================
-- ARGUS migration 005 — portfolios, signals, alerts, system logs
-- Hypertable when TimescaleDB is installed (plain table with supporting
-- indexes otherwise): system_logs (on time).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- portfolios
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolios (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name       TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_portfolios_user ON portfolios (user_id);

-- ---------------------------------------------------------------------------
-- portfolio_positions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_positions (
    portfolio_id UUID    NOT NULL REFERENCES portfolios (id) ON DELETE CASCADE,
    asset_id     UUID    NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    qty          NUMERIC NOT NULL,
    cost_basis   NUMERIC,
    PRIMARY KEY (portfolio_id, asset_id)
);

CREATE INDEX IF NOT EXISTS idx_positions_portfolio ON portfolio_positions (portfolio_id);
CREATE INDEX IF NOT EXISTS idx_positions_asset     ON portfolio_positions (asset_id);

-- ---------------------------------------------------------------------------
-- signals — derived from locked prediction records
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    as_of         TIMESTAMPTZ NOT NULL,
    asset_id      UUID        NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    signal_type   TEXT        NOT NULL,
    strength      DOUBLE PRECISION,
    horizon       INT,
    prediction_id UUID        REFERENCES predictions (id) ON DELETE SET NULL,
    payload       JSONB
);

CREATE INDEX IF NOT EXISTS idx_signals_asset      ON signals (asset_id);
CREATE INDEX IF NOT EXISTS idx_signals_asof       ON signals (as_of DESC);
CREATE INDEX IF NOT EXISTS idx_signals_type       ON signals (signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_prediction ON signals (prediction_id);

-- ---------------------------------------------------------------------------
-- alerts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alerts (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    alert_type   TEXT        NOT NULL,
    asset_id     UUID        REFERENCES assets (id) ON DELETE SET NULL,
    title        TEXT        NOT NULL,
    body         TEXT,
    severity     TEXT        NOT NULL DEFAULT 'INFO',
    channel      TEXT        NOT NULL CHECK (channel IN ('PUSH', 'EMAIL', 'IN_APP')),
    delivered_at TIMESTAMPTZ,
    read_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_alerts_user   ON alerts (user_id);
CREATE INDEX IF NOT EXISTS idx_alerts_asset  ON alerts (asset_id);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts (created_at DESC);

-- ---------------------------------------------------------------------------
-- alert_rules
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS alert_rules (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    rule_type  TEXT        NOT NULL,
    asset_id   UUID        REFERENCES assets (id) ON DELETE CASCADE,
    params     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    is_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alert_rules_user  ON alert_rules (user_id);
CREATE INDEX IF NOT EXISTS idx_alert_rules_asset ON alert_rules (asset_id);

-- ---------------------------------------------------------------------------
-- system_logs — operational logs. Hypertable on time when TimescaleDB is
-- installed (plain table with supporting indexes otherwise).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_logs (
    time    TIMESTAMPTZ NOT NULL,
    level   TEXT        NOT NULL,
    service TEXT        NOT NULL,
    message TEXT        NOT NULL,
    context JSONB
);

-- Hypertable when TimescaleDB is installed, plain table + supporting index otherwise.
SELECT argus_maybe_hypertable('system_logs', 'time', 7 * 86400000000);  -- 7 days in microseconds

CREATE INDEX IF NOT EXISTS idx_logs_time    ON system_logs (time DESC);
CREATE INDEX IF NOT EXISTS idx_logs_service ON system_logs (service, time DESC);

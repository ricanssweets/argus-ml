-- =============================================================================
-- ARGUS migration 003 — market & alternative data
-- Hypertables when TimescaleDB is installed (7-day chunks = 7 * 86400000000
-- microseconds), plain tables with supporting indexes otherwise:
--   prices, options_chains, macro_data, sentiment, market_regimes
-- Regular tables (point-in-time rows, not append-only series):
--   fundamentals, earnings, news
-- Composite (asset_id, time DESC) indexes on every hypertable (or the
-- series-key equivalent where a table has no asset_id).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- prices — OHLCV bars. Hypertable on time. PK (time, asset_id).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS prices (
    time         TIMESTAMPTZ    NOT NULL,
    asset_id     UUID           NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    open         DOUBLE PRECISION,
    high         DOUBLE PRECISION,
    low          DOUBLE PRECISION,
    close        DOUBLE PRECISION,
    adj_close    DOUBLE PRECISION,
    volume       BIGINT,
    data_source  TEXT,
    data_version INT            NOT NULL DEFAULT 1,
    PRIMARY KEY (time, asset_id)
);

-- Hypertable when TimescaleDB is installed, plain table + supporting index otherwise.
SELECT argus_maybe_hypertable('prices', 'time', 7 * 86400000000);  -- 7 days in microseconds

CREATE INDEX IF NOT EXISTS idx_prices_asset_time ON prices (asset_id, time DESC);

-- ---------------------------------------------------------------------------
-- fundamentals — point-in-time quarterly/annual snapshots. Regular table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS fundamentals (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id          UUID        NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    fiscal_period     DATE        NOT NULL,
    reported_at       TIMESTAMPTZ NOT NULL,
    revenue           NUMERIC,
    eps               NUMERIC,
    ebitda            NUMERIC,
    fcf               NUMERIC,
    gross_margin      DOUBLE PRECISION,
    net_margin        DOUBLE PRECISION,
    roe               DOUBLE PRECISION,
    roic              DOUBLE PRECISION,
    total_debt        NUMERIC,
    cash              NUMERIC,
    interest_coverage DOUBLE PRECISION,
    pe                DOUBLE PRECISION,
    forward_pe        DOUBLE PRECISION,
    peg               DOUBLE PRECISION,
    ev_ebitda         DOUBLE PRECISION,
    ps                DOUBLE PRECISION,
    pb                DOUBLE PRECISION,
    fcf_yield         DOUBLE PRECISION,
    UNIQUE (asset_id, fiscal_period, reported_at)
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_asset   ON fundamentals (asset_id);
CREATE INDEX IF NOT EXISTS idx_fundamentals_period  ON fundamentals (asset_id, fiscal_period DESC);

-- ---------------------------------------------------------------------------
-- earnings — announcements. Regular table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS earnings (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id         UUID        NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    announce_date    TIMESTAMPTZ NOT NULL,
    fiscal_quarter   TEXT,
    eps_actual       NUMERIC,
    eps_estimate     NUMERIC,
    revenue_actual   NUMERIC,
    revenue_estimate NUMERIC,
    surprise_pct     DOUBLE PRECISION,
    guidance_text    TEXT
);

CREATE INDEX IF NOT EXISTS idx_earnings_asset ON earnings (asset_id);
CREATE INDEX IF NOT EXISTS idx_earnings_date  ON earnings (asset_id, announce_date DESC);

-- ---------------------------------------------------------------------------
-- options_chains — daily snapshots (+ intraday when available). Hypertable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS options_chains (
    time          TIMESTAMPTZ    NOT NULL,
    asset_id      UUID           NOT NULL REFERENCES assets (id) ON DELETE CASCADE,
    expiration    DATE           NOT NULL,
    strike        DOUBLE PRECISION NOT NULL,
    option_type   TEXT           NOT NULL CHECK (option_type IN ('CALL', 'PUT')),
    iv            DOUBLE PRECISION,
    delta         DOUBLE PRECISION,
    gamma         DOUBLE PRECISION,
    theta         DOUBLE PRECISION,
    vega          DOUBLE PRECISION,
    open_interest BIGINT,
    volume        BIGINT
);

-- Hypertable when TimescaleDB is installed, plain table + supporting index otherwise.
SELECT argus_maybe_hypertable('options_chains', 'time', 7 * 86400000000);  -- 7 days in microseconds

CREATE INDEX IF NOT EXISTS idx_options_asset_time ON options_chains (asset_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_options_expiry     ON options_chains (asset_id, expiration, strike);

-- ---------------------------------------------------------------------------
-- macro_data — FRED-style series (FEDFUNDS, DGS10, CPIAUCSL, UNRATE, VIX...).
-- Hypertable on time.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS macro_data (
    time      TIMESTAMPTZ NOT NULL,
    series_id TEXT        NOT NULL,
    value     DOUBLE PRECISION,
    source    TEXT
);

-- Hypertable when TimescaleDB is installed, plain table + supporting index otherwise.
SELECT argus_maybe_hypertable('macro_data', 'time', 7 * 86400000000);  -- 7 days in microseconds

CREATE INDEX IF NOT EXISTS idx_macro_series_time ON macro_data (series_id, time DESC);

-- ---------------------------------------------------------------------------
-- news — headlines. Regular table.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS news (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id     UUID        REFERENCES assets (id) ON DELETE CASCADE,
    published_at TIMESTAMPTZ NOT NULL,
    source       TEXT,
    headline     TEXT        NOT NULL,
    url          TEXT,
    symbols      TEXT[]
);

CREATE INDEX IF NOT EXISTS idx_news_asset ON news (asset_id);
CREATE INDEX IF NOT EXISTS idx_news_pub   ON news (published_at DESC);

-- ---------------------------------------------------------------------------
-- sentiment — aggregate sentiment snapshots. Hypertable on time.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sentiment (
    time          TIMESTAMPTZ NOT NULL,
    asset_id      UUID        REFERENCES assets (id) ON DELETE CASCADE,
    scope         TEXT        NOT NULL CHECK (scope IN ('ASSET', 'SECTOR', 'MARKET')),
    polarity      DOUBLE PRECISION,
    magnitude     DOUBLE PRECISION,
    velocity      DOUBLE PRECISION,
    news_count    INT,
    source_model  TEXT,
    model_version TEXT
);

-- Hypertable when TimescaleDB is installed, plain table + supporting index otherwise.
SELECT argus_maybe_hypertable('sentiment', 'time', 7 * 86400000000);  -- 7 days in microseconds

CREATE INDEX IF NOT EXISTS idx_sentiment_asset_time ON sentiment (asset_id, time DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_scope_time ON sentiment (scope, time DESC);

-- ---------------------------------------------------------------------------
-- market_regimes — one row per day for the market aggregate. Hypertable.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_regimes (
    time       TIMESTAMPTZ NOT NULL,
    regime     TEXT        NOT NULL,
    confidence DOUBLE PRECISION,
    drivers    JSONB
);

-- Hypertable when TimescaleDB is installed, plain table + supporting index otherwise.
SELECT argus_maybe_hypertable('market_regimes', 'time', 7 * 86400000000);  -- 7 days in microseconds

CREATE INDEX IF NOT EXISTS idx_regimes_time ON market_regimes (time DESC);

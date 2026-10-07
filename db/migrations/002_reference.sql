-- =============================================================================
-- ARGUS migration 002 — reference data
-- Idempotent: CREATE TABLE / INDEX IF NOT EXISTS.
-- Conventions: id UUID PK DEFAULT gen_random_uuid(), TIMESTAMPTZ,
-- NUMERIC for money, DOUBLE PRECISION for prices, snake_case, all FKs indexed.
-- =============================================================================

-- Local profile mirror (auth is handled by Supabase)
CREATE TABLE IF NOT EXISTS users (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT        NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- One row per tradable instrument
CREATE TABLE IF NOT EXISTS assets (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker      TEXT        NOT NULL UNIQUE,
    name        TEXT        NOT NULL,
    asset_type  TEXT        NOT NULL CHECK (asset_type IN ('STOCK', 'ETF', 'INDEX', 'CRYPTO', 'FUTURE')),
    exchange    TEXT,
    currency    TEXT        NOT NULL DEFAULT 'USD',
    sector      TEXT,
    industry    TEXT,
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_assets_type   ON assets (asset_type);
CREATE INDEX IF NOT EXISTS idx_assets_sector  ON assets (sector);

-- One-to-one extension of assets for stocks
CREATE TABLE IF NOT EXISTS stocks (
    asset_id            UUID    PRIMARY KEY REFERENCES assets (id) ON DELETE CASCADE,
    shares_outstanding  NUMERIC,
    float_shares        NUMERIC
);

-- One-to-one extension of assets for ETFs
CREATE TABLE IF NOT EXISTS etfs (
    asset_id        UUID    PRIMARY KEY REFERENCES assets (id) ON DELETE CASCADE,
    issuer          TEXT,
    expense_ratio   NUMERIC,
    aum             NUMERIC,
    inception_date  DATE,
    dividend_yield  DOUBLE PRECISION,
    tracking_index  TEXT
);

-- ETF constituents, point-in-time snapshots
CREATE TABLE IF NOT EXISTS etf_holdings (
    etf_id          UUID    NOT NULL REFERENCES etfs (asset_id) ON DELETE CASCADE,
    holding_asset_id UUID   REFERENCES assets (id) ON DELETE SET NULL,
    holding_ticker  TEXT    NOT NULL,
    weight          NUMERIC NOT NULL CHECK (weight >= 0 AND weight <= 1),
    as_of           DATE    NOT NULL,
    PRIMARY KEY (etf_id, holding_ticker, as_of)
);

CREATE INDEX IF NOT EXISTS idx_etf_holdings_etf      ON etf_holdings (etf_id);
CREATE INDEX IF NOT EXISTS idx_etf_holdings_asset    ON etf_holdings (holding_asset_id);
CREATE INDEX IF NOT EXISTS idx_etf_holdings_asof     ON etf_holdings (as_of DESC);

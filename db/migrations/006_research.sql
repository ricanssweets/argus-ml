-- =============================================================================
-- ARGUS migration 006 — research schema (backtesting)
-- All tables are schema-qualified under research.* — physically separate
-- from public.* so research never contaminates live prediction records.
-- The research schema itself is created in 001_extensions.sql.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- research.backtests — strategy configurations and runs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research.backtests (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name       TEXT        NOT NULL,
    config     JSONB       NOT NULL,
    universe   TEXT[],
    start_date DATE         NOT NULL,
    end_date   DATE         NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- research.backtest_trades — simulated trades for a backtest
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research.backtest_trades (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    backtest_id UUID        NOT NULL REFERENCES research.backtests (id) ON DELETE CASCADE,
    asset_id    UUID        NOT NULL REFERENCES public.assets (id) ON DELETE CASCADE,
    entry_time  TIMESTAMPTZ NOT NULL,
    exit_time   TIMESTAMPTZ,
    side        TEXT        NOT NULL CHECK (side IN ('LONG', 'SHORT')),
    qty         NUMERIC     NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    exit_price  DOUBLE PRECISION,
    pnl         NUMERIC,
    costs       NUMERIC,
    bars_held   INT,
    exit_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_bt_trades_backtest ON research.backtest_trades (backtest_id);
CREATE INDEX IF NOT EXISTS idx_bt_trades_asset    ON research.backtest_trades (asset_id);
CREATE INDEX IF NOT EXISTS idx_bt_trades_entry    ON research.backtest_trades (backtest_id, entry_time);

-- ---------------------------------------------------------------------------
-- research.backtest_metrics — one full metric set per backtest
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS research.backtest_metrics (
    backtest_id      UUID PRIMARY KEY REFERENCES research.backtests (id) ON DELETE CASCADE,
    cagr             DOUBLE PRECISION,
    total_return     DOUBLE PRECISION,
    sharpe           DOUBLE PRECISION,
    sortino          DOUBLE PRECISION,
    max_drawdown     DOUBLE PRECISION,
    win_rate         DOUBLE PRECISION,
    profit_factor    DOUBLE PRECISION,
    avg_win          NUMERIC,
    avg_loss         NUMERIC,
    expectancy       DOUBLE PRECISION,
    recovery_factor  DOUBLE PRECISION,
    n_trades         INT,
    exposure         DOUBLE PRECISION,
    monthly_returns  JSONB,
    benchmark        TEXT,
    alpha            DOUBLE PRECISION,
    beta             DOUBLE PRECISION,
    information_ratio DOUBLE PRECISION
);

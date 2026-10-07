"""Research assistant's queryable record store (SQLite).

ARGUS Phases 6. The research assistant answers ONLY from stored platform
records. In production this module would query Postgres/TimescaleDB using
the migration schemas (004_predictions.sql, 006_research.sql). Since no
live Postgres exists in this build, it uses a local SQLite file whose
tables mirror the real schema names: ``assets``, ``predictions``,
``prediction_outcomes``, ``research_backtests`` (mirrors
``research.backtests`` + metrics), ``model_metrics_daily``.

Every seeded row carries data_status='SYNTHETIC_FIXTURE' — clearly
synthetic fixtures, never real market data. The router maps that to the
API ``data_status`` value and labels the answer as synthetic.

DB path: ``services/ml/data/research_store.db``, overridable with the
``ARGUS_RESEARCH_DB`` env var (used by tests to point at an empty DB).

Production backend: when ``DATABASE_URL`` is set, the store reads from
Postgres mirror tables (``svc.research_*`` — same shape as the SQLite
schema, created by ``app.db.ensure_mirror``) instead of SQLite. The
planner SQL is table-name-rewritten; behavior and honesty guarantees are
identical.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    ticker      TEXT NOT NULL UNIQUE,
    name        TEXT,
    asset_type  TEXT,          -- STOCK | ETF
    sector      TEXT,
    industry    TEXT,
    sp500       INTEGER DEFAULT 0,  -- 1 = S&P 500 constituent (demo flag)
    pe_ratio    REAL,
    data_status TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE'
);

CREATE TABLE IF NOT EXISTS predictions (
    id              TEXT PRIMARY KEY,
    as_of           TEXT NOT NULL,
    asset_id        TEXT NOT NULL REFERENCES assets (id),
    horizon         INTEGER NOT NULL,
    model_version   TEXT NOT NULL,
    regime          TEXT,
    p_positive      REAL,
    p_negative      REAL,
    expected_return REAL,
    expected_vol    REAL,
    expected_high   REAL,
    expected_low    REAL,
    ci_lower        REAL,
    ci_upper        REAL,
    downside_risk   REAL,
    upside_potential REAL,
    risk_reward     REAL,
    composite_score REAL,
    trend_strength  REAL,
    momentum_score  REAL,
    fundamental_score REAL,
    risk_score      REAL,
    volatility_score REAL,
    confidence      REAL,
    engine_signals  TEXT,      -- JSON object {A:.., B:.., ...}
    explanation     TEXT,      -- JSON
    data_status_overall TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE',
    UNIQUE (as_of, asset_id, horizon, model_version)
);

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    id               TEXT PRIMARY KEY,
    prediction_id    TEXT NOT NULL UNIQUE REFERENCES predictions (id),
    observed_at      TEXT NOT NULL,
    actual_return    REAL,
    direction_correct INTEGER,   -- 0/1
    data_status      TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE'
);

-- Mirrors research.backtests (+ research.backtest_metrics summary columns).
CREATE TABLE IF NOT EXISTS research_backtests (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    config      TEXT,            -- JSON
    universe    TEXT,            -- JSON list
    start_date  TEXT,
    end_date    TEXT,
    created_at  TEXT,
    cagr        REAL,
    sharpe      REAL,
    max_drawdown REAL,
    win_rate    REAL,
    n_trades    INTEGER,
    data_status TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE'
);

CREATE TABLE IF NOT EXISTS model_metrics_daily (
    day                  TEXT NOT NULL,
    model_version        TEXT NOT NULL,
    horizon              INTEGER NOT NULL,
    regime               TEXT,
    sector               TEXT,
    n                    INTEGER,
    accuracy             REAL,
    precision            REAL,
    recall               REAL,
    f1                   REAL,
    roc_auc              REAL,
    brier                REAL,
    log_loss             REAL,
    cal_error            REAL,
    mae                  REAL,
    rmse                 REAL,
    directional_accuracy REAL,
    data_status          TEXT NOT NULL DEFAULT 'SYNTHETIC_FIXTURE',
    PRIMARY KEY (day, model_version, horizon, regime, sector)
);
"""


def db_path() -> str:
    env = os.environ.get("ARGUS_RESEARCH_DB")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "data", "research_store.db")


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    p = path or db_path()
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    return con


def backend() -> str:
    """'postgres' when DATABASE_URL is set, else 'sqlite'."""
    from app import db as _db
    return "postgres" if _db.db_configured() else "sqlite"


# Table rewrite for the Postgres mirror (longest names first so word
# boundaries can't partially match).
_PG_TABLES = (
    ("prediction_outcomes", "svc.research_prediction_outcomes"),
    ("research_backtests", "svc.research_backtests"),
    ("model_metrics_daily", "svc.research_model_metrics_daily"),
    ("predictions", "svc.research_predictions"),
    ("assets", "svc.research_assets"),
)


def _to_pg_sql(sql: str) -> str:
    import re
    out = sql
    for name, qual in _PG_TABLES:
        out = re.sub(r"\b" + name + r"\b", qual, out)
    return out


def init_schema(path: Optional[str] = None) -> str:
    """Create tables (no rows). Returns the db path (or 'postgres')."""
    if backend() == "postgres":
        from app import db as _db
        _db.ensure_mirror()
        return "postgres:svc mirror"
    p = path or db_path()
    con = connect(p)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()
    return p


def query(sql: str, params: tuple = (), path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Run a read-only SELECT and return rows as dicts.

    Raises RuntimeError on a non-SELECT statement (the planner may only
    read the store). Uses Postgres mirror tables when DATABASE_URL is set.
    """
    if not sql.lstrip().upper().startswith("SELECT"):
        raise RuntimeError("research store is read-only: only SELECT allowed")
    if backend() == "postgres":
        from app import db as _db
        return _db.fetchall(_to_pg_sql(sql), params)
    con = connect(path)
    try:
        cur = con.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        con.close()


def engine_signals_of(row: Dict[str, Any]) -> Dict[str, float]:
    try:
        return {k: float(v) for k, v in json.loads(row.get("engine_signals") or "{}").items()}
    except (ValueError, TypeError, AttributeError):
        return {}

"""Pydantic v2 schemas matching API_SPEC.md response shapes.

Every API response is wrapped in the envelope:
    {"data": ..., "meta": {"as_of", "data_status", "request_id"}}
Errors use: {"error": {"code", "message", "details"}} (built in main.py).
Every payload carries `disclaimer` (where the spec requires it) and
`data_status`.
"""

from __future__ import annotations

from typing import Any, Dict, Generic, List, Literal, Optional, TypeVar

from pydantic import BaseModel, Field

DataStatus = Literal["CONFIRMED", "LIKELY", "UNCONFIRMED", "CONFLICTING",
                    "MISSING", "SYNTHETIC_FIXTURE"]
# SYNTHETIC_FIXTURE: clearly-labeled synthetic test fixtures (research
# store seed rows). Never real market data; answers built from them say so.

DISCLAIMER = "Probabilistic estimate. Not a guarantee of future performance."

T = TypeVar("T")


class Meta(BaseModel):
    as_of: str
    data_status: DataStatus
    request_id: str


class Envelope(BaseModel, Generic[T]):
    data: T
    meta: Meta


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


# ------------------------------------------------------------------ assets
class AssetSummary(BaseModel):
    ticker: str
    name: str
    asset_type: str
    exchange: Optional[str] = None
    currency: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    is_active: bool = True


class AssetDetail(AssetSummary):
    price: Optional[float] = None
    prev_close: Optional[float] = None
    change_pct: Optional[float] = None
    market_cap: Optional[float] = None
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


class PriceBar(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[int] = None


class PricesResponse(BaseModel):
    ticker: str
    interval: str = "1d"
    bars: List[PriceBar]
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


class FundamentalsResponse(BaseModel):
    ticker: str
    fiscal_period: Optional[str] = None
    reported_at: Optional[str] = None
    revenue: Optional[float] = None
    eps: Optional[float] = None
    ebitda: Optional[float] = None
    gross_margin: Optional[float] = None
    net_margin: Optional[float] = None
    roe: Optional[float] = None
    pe: Optional[float] = None
    forward_pe: Optional[float] = None
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


class TechnicalsResponse(BaseModel):
    ticker: str
    as_of: str
    rsi_14: Optional[float] = None
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    bollinger_upper: Optional[float] = None
    bollinger_lower: Optional[float] = None
    atr_14: Optional[float] = None
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


# -------------------------------------------------------------- predictions
class Explanation(BaseModel):
    reasons: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    invalidated_if: List[str] = Field(default_factory=list)


class Prediction(BaseModel):
    ticker: str
    horizon: int
    as_of: str
    model_version: str
    regime: str
    p_positive: float = Field(ge=0, le=1)
    p_negative: float = Field(ge=0, le=1)
    expected_return: Optional[float] = None
    expected_vol: Optional[float] = None
    expected_high: Optional[float] = None
    expected_low: Optional[float] = None
    ci: List[float] = Field(default_factory=list)  # [lower, upper]
    downside_risk: Optional[float] = None
    upside_potential: Optional[float] = None
    risk_reward: Optional[float] = None
    composite_score: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    data_status: DataStatus = "UNCONFIRMED"
    explanation: Explanation = Field(default_factory=Explanation)
    disclaimer: str = DISCLAIMER
    # Phase 4-9 additions (all optional, additive):
    engine_signals: Optional[Dict[str, Any]] = None  # per-engine detail
    pipeline: Optional[str] = None  # "live" | "synthetic_stub"
    calibrated: Optional[bool] = None  # isotonic map applied?


class PredictionRequest(BaseModel):
    ticker: str
    horizons: List[int] = Field(default_factory=lambda: [1, 5, 20])
    model_version: str = "latest"


class JobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "done", "failed"]
    ticker: Optional[str] = None
    horizons: List[int] = Field(default_factory=list)
    result: Optional[Any] = None
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------- signals
class Signal(BaseModel):
    id: str
    as_of: str
    ticker: str
    signal_type: str
    strength: float = Field(ge=0, le=1)
    horizon: int
    setup_tags: List[str] = Field(default_factory=list)
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


class ScannerRow(BaseModel):
    ticker: str
    horizon: int
    composite_score: int
    p_positive: float
    expected_return: Optional[float] = None
    risk_reward: Optional[float] = None
    setup_tags: List[str] = Field(default_factory=list)
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


class MarketRegime(BaseModel):
    regime: str
    confidence: float = Field(ge=0, le=1)
    as_of: str
    drivers: List[str] = Field(default_factory=list)
    history: List[Dict[str, Any]] = Field(default_factory=list)
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


# -------------------------------------------------------------------- etf
class EtfHolding(BaseModel):
    ticker: str
    name: Optional[str] = None
    weight: float


class EtfProfile(BaseModel):
    ticker: str
    name: str
    issuer: Optional[str] = None
    expense_ratio: Optional[float] = None
    aum: Optional[float] = None
    holdings: List[EtfHolding] = Field(default_factory=list)
    sector_exposure: List[Dict[str, Any]] = Field(default_factory=list)
    concentration_top10: Optional[float] = None
    tracking_error: Optional[float] = None
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


# --------------------------------------------------------------- portfolio
class Position(BaseModel):
    ticker: str
    qty: float
    cost_basis: Optional[float] = None


class PortfolioAnalyzeRequest(BaseModel):
    positions: List[Position]
    scenarios: List[str] = Field(default_factory=list)


class PortfolioAnalysis(BaseModel):
    expected_return: Optional[float] = None
    volatility: Optional[float] = None
    max_drawdown: Optional[float] = None
    beta: Optional[float] = None
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    var_95: Optional[float] = None
    cvar_95: Optional[float] = None
    var_99: Optional[float] = None
    cvar_99: Optional[float] = None
    weights: Optional[Dict[str, float]] = None
    correlation_matrix: Optional[Dict[str, Dict[str, float]]] = None
    sector_concentration: List[Dict[str, Any]] = Field(default_factory=list)
    sector_hhi: Optional[float] = None
    diversification_score: Optional[float] = None
    factor_exposure: Optional[Dict[str, Any]] = None
    vol_target_sizing: Optional[Dict[str, Any]] = None
    stress_tests: List[Dict[str, Any]] = Field(default_factory=list)
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER
    notes: List[str] = Field(default_factory=list)


# --------------------------------------------------------------- backtests
class BacktestRequest(BaseModel):
    name: str = "stub-backtest"
    universe: List[str] = Field(default_factory=list)
    start: str = "2024-01-01"
    end: str = "2025-12-31"
    entry_rules: Dict[str, Any] = Field(default_factory=dict)
    exit_rules: Dict[str, Any] = Field(default_factory=dict)
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    position_sizing: Dict[str, Any] = Field(default_factory=dict)
    slippage_bps: float = 5.0
    commission: float = 0.0
    allow_short: bool = False


class BacktestMetrics(BaseModel):
    cagr: Optional[float] = None
    total_return: Optional[float] = None
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    max_drawdown: Optional[float] = None
    win_rate: Optional[float] = None
    profit_factor: Optional[float] = None
    expectancy: Optional[float] = None
    n_trades: Optional[int] = None
    benchmark: str = "SPY"


class BacktestResult(BaseModel):
    backtest_id: str
    name: str
    status: Literal["queued", "running", "done", "failed"] = "done"
    config: Dict[str, Any] = Field(default_factory=dict)
    metrics: Optional[BacktestMetrics] = None
    equity_curve: List[Dict[str, Any]] = Field(default_factory=list)
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER
    notes: List[str] = Field(default_factory=list)


# ------------------------------------------------------------------ models
class ModelVersionInfo(BaseModel):
    version: str
    family: str = "ENSEMBLE"
    status: str = "CHAMPION"
    horizons: List[int] = Field(default_factory=list)
    weights_are_priors: bool = True
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


class ModelMetrics(BaseModel):
    version: str
    horizon: int
    group_by: str = "regime"
    n: int = 0
    brier: Optional[float] = None
    roc_auc: Optional[float] = None
    calibration_error: Optional[float] = None
    by_group: List[Dict[str, Any]] = Field(default_factory=list)
    degradation_flags: List[Dict[str, Any]] = Field(default_factory=list)
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


class CalibrationCurve(BaseModel):
    version: str
    horizon: int
    method: str = "ISOTONIC"
    buckets: List[Dict[str, Any]] = Field(default_factory=list)
    min_samples: int = 20
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


# ---------------------------------------------------------------- research
class ResearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)


class Citation(BaseModel):
    table: str
    id: str
    as_of: str


class ResearchResponse(BaseModel):
    answer: str
    citations: List[Citation] = Field(default_factory=list)
    sql_used: Optional[str] = None
    data_status: DataStatus = "UNCONFIRMED"
    disclaimer: str = DISCLAIMER


# ----------------------------------------------------------------- alerts
class Alert(BaseModel):
    id: str
    created_at: str
    alert_type: str
    ticker: Optional[str] = None
    title: str
    body: str
    severity: str = "INFO"
    channel: str = "IN_APP"
    read_at: Optional[str] = None


class AlertRuleCreate(BaseModel):
    rule_type: str
    ticker: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class AlertRule(BaseModel):
    id: str
    rule_type: str
    ticker: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    data_status: DataStatus = "UNCONFIRMED"

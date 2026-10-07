"""argus_quant - deterministic quantitative library for the ARGUS platform.

Modules: indicators, features, engines, validation, metrics, calibration,
backtest, montecarlo, risk.

Locked laws (see docs/ARCHITECTURE.md):
  * No future leak: every indicator/feature at bar t uses bars <= t only.
  * Calibrators fit ONLY on out-of-fold predictions.
  * Calibration buckets need MIN_SAMPLES_PER_BUCKET = 20.
  * Missing data is reported (data_status), never fabricated.
"""

from . import (alerts, backtest, calibration, data_connectors, engines,
               features, indicators, metrics, montecarlo, portfolio, regime,
               risk, validation)
from .metrics import MIN_SAMPLES_PER_BUCKET

__version__ = "0.1.0"
__all__ = [
    "alerts", "indicators", "features", "engines", "validation", "metrics",
    "calibration", "backtest", "montecarlo", "risk", "portfolio", "regime",
    "data_connectors",
    "MIN_SAMPLES_PER_BUCKET",
]

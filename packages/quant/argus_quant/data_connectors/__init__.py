"""Free-data connectors for ARGUS Phase 4 engines.

Modules:
  edgar     - SEC EDGAR company facts (fundamentals) + submissions (8-K)
  fred      - FRED macro series (CSV, no key)
  options   - yfinance option chains (delayed; graceful MISSING on failure)
  sentiment - deterministic finance-lexicon sentiment scorer

All connectors are cache-first under ``~/workspace/argus/data/`` and
point-in-time safe: every helper that takes ``as_of`` returns only data
whose filing/observation timestamp is <= ``as_of``.
"""

from . import edgar, fred, options, sentiment

__all__ = ["edgar", "fred", "options", "sentiment"]

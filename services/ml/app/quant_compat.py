"""Defensive import shim for the argus_quant package.

The shared quant library lives at ~/workspace/argus/packages/quant and is
built by a parallel track. This service must NEVER duplicate quant logic —
it imports argus_quant if importable and degrades gracefully (with
data_status=UNCONFIRMED stub responses) when it is not yet available.

Usage:
    from app.quant_compat import argus_quant, QUANT_AVAILABLE, get_quant
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("argus.ml.quant_compat")

# Canonical location of the shared quant package (monorepo layout).
QUANT_DIR = Path.home() / "workspace" / "argus" / "packages" / "quant"

argus_quant: Optional[Any] = None

try:  # 1) normal import (installed package / PYTHONPATH)
    import argus_quant as _aq  # type: ignore[import-not-found]

    argus_quant = _aq
    log.info("argus_quant imported from installed path")
except ImportError:
    # 2) defensive fallback: add the monorepo package dir to sys.path
    if str(QUANT_DIR) not in sys.path:
        sys.path.insert(0, str(QUANT_DIR))
    try:
        import argus_quant as _aq2  # type: ignore[import-not-found]

        argus_quant = _aq2
        log.info("argus_quant imported via sys.path fallback (%s)", QUANT_DIR)
    except ImportError:
        log.warning(
            "argus_quant not available (packages/quant not yet populated). "
            "ML service will run in DEGRADED/demo mode with UNCONFIRMED stub "
            "data. No quant logic is duplicated here."
        )

QUANT_AVAILABLE: bool = argus_quant is not None


def get_quant() -> Optional[Any]:
    """Return the argus_quant module, or None if unavailable."""
    return argus_quant


def quant_attr(dotted: str) -> Optional[Any]:
    """Safely resolve e.g. 'risk.value_at_risk' inside argus_quant.

    Returns None if the module or attribute is missing — callers must
    handle None by returning data_status=UNCONFIRMED stub payloads.
    """
    obj: Any = argus_quant
    if obj is None:
        return None
    for part in dotted.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj

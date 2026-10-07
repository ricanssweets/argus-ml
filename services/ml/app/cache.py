"""Tiny optional Redis cache for the ml service (Phase 9).

Used for prediction responses per docs/PERFORMANCE.md. Graceful when Redis
is absent or unreachable: every function degrades to a no-op miss and the
service keeps serving from the primary path. See docs/PERFORMANCE.md for
the key scheme (``pred:{ticker}:{horizon}:{asof_date}``) and TTL policy.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

log = logging.getLogger("argus.ml")

_client = None
_unavailable_until = 0.0
_warned = False


def _redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def cache_ttl() -> int:
    try:
        return int(os.environ.get("PRED_CACHE_TTL", "300"))
    except ValueError:
        return 300


def prediction_key(ticker: str, horizon: int, asof_date: str) -> str:
    """pred:{ticker}:{horizon}:{asof_date} — see docs/PERFORMANCE.md."""
    return f"pred:{ticker.upper()}:{int(horizon)}:{asof_date}"


def get_client():
    """Lazily connect; return None when Redis is unavailable (graceful)."""
    global _client, _unavailable_until, _warned
    now = time.monotonic()
    if _client is not None:
        return _client
    if now < _unavailable_until:
        return None
    try:
        import redis
        client = redis.Redis.from_url(_redis_url(), socket_connect_timeout=1,
                                      socket_timeout=2)
        client.ping()
        _client = client
        return _client
    except Exception as e:  # noqa: BLE001 — Redis is optional
        _unavailable_until = now + 60.0  # back off before retrying
        if not _warned:
            log.warning("Redis unavailable (%s) — prediction cache disabled, "
                        "serving without cache", e)
            _warned = True
        return None


def cache_get(key: str) -> Optional[dict]:
    client = get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        return json.loads(raw) if raw else None
    except Exception:  # noqa: BLE001 — cache must never break serving
        return None


def cache_set(key: str, value: dict, ttl: int | None = None) -> bool:
    client = get_client()
    if client is None:
        return False
    try:
        client.set(key, json.dumps(value), ex=ttl if ttl is not None else cache_ttl())
        return True
    except Exception:  # noqa: BLE001
        return False


def reset_for_tests() -> None:
    """Clear cached client state (tests only)."""
    global _client, _unavailable_until, _warned
    _client = None
    _unavailable_until = 0.0
    _warned = False

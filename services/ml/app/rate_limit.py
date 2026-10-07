"""Distributed sliding-window rate limiter (production hardening).

Primary backend: Redis (``REDIS_URL``) with an atomic Lua script over a
sorted set per (bucket, identity) key. Falls back to the in-process
sliding window when Redis is unset or unreachable — with a LOUD log so
the degraded mode is never mistaken for the real one.

Key scheme: ``rl:{bucket}:{identity}`` — members are unique request IDs,
scores are epoch milliseconds. TTL = window length.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections import defaultdict, deque
from typing import Dict, Tuple

log = logging.getLogger("argus.ml.ratelimit")

_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
if count < limit then
  redis.call('ZADD', key, now, member)
  redis.call('PEXPIRE', key, window)
  return {1, limit - count - 1, 0}
else
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry = 0
  if #oldest >= 2 then
    retry = math.max(1, math.ceil((tonumber(oldest[2]) + window - now) / 1000))
  end
  return {0, 0, retry}
end
"""


class RedisSlidingWindow:
    """Redis-backed sliding window. Raises on connection failure."""

    def __init__(self, url: str):
        import redis
        self._client = redis.Redis.from_url(
            url, socket_connect_timeout=2, socket_timeout=3)
        self._client.ping()  # fail fast when unreachable
        self._sha = self._client.script_load(_LUA)

    def check(self, bucket: str, identity: str, limit: int,
              window_ms: int = 60_000) -> Tuple[bool, int, int]:
        """Return (allowed, remaining, retry_after_seconds)."""
        key = f"rl:{bucket}:{identity}"
        now_ms = int(time.time() * 1000)
        member = f"{now_ms}:{uuid.uuid4().hex}"
        allowed, remaining, retry = self._client.evalsha(
            self._sha, 1, key, now_ms, window_ms, limit, member)
        return bool(allowed), int(remaining), int(retry)


class MemorySlidingWindow:
    """In-process sliding window (single replica / dev fallback)."""

    def __init__(self):
        self._hits: Dict[Tuple[str, str], deque] = defaultdict(deque)

    def check(self, bucket: str, identity: str, limit: int,
              window_ms: int = 60_000) -> Tuple[bool, int, int]:
        key = (bucket, identity)
        now = time.monotonic()
        window_s = window_ms / 1000.0
        dq = self._hits[key]
        while dq and dq[0] <= now - window_s:
            dq.popleft()
        if len(dq) >= limit:
            retry_after = int(dq[0] + window_s - now) + 1
            return False, 0, retry_after
        dq.append(now)
        return True, limit - len(dq), 0


_backend = None
_backend_kind = None
_warned = False


def get_backend():
    """Redis backend when REDIS_URL works, else in-process (loud)."""
    global _backend, _backend_kind, _warned
    if _backend is not None:
        return _backend
    url = os.environ.get("REDIS_URL")
    if url:
        try:
            _backend = RedisSlidingWindow(url)
            _backend_kind = "redis"
            log.info("rate limiter: Redis backend active (%s)",
                     url.split("@")[-1])
            return _backend
        except Exception as exc:  # noqa: BLE001 — degrade loudly
            log.warning("rate limiter: Redis unreachable (%s) — falling back "
                        "to IN-PROCESS limiter (per-replica only)", exc)
    elif not _warned:
        log.warning("rate limiter: REDIS_URL not set — using IN-PROCESS "
                    "limiter. Multi-replica deploys need REDIS_URL for a "
                    "shared limit.")
        _warned = True
    _backend = MemorySlidingWindow()
    _backend_kind = "memory"
    return _backend


def backend_kind() -> str:
    get_backend()
    return _backend_kind or "memory"


def reset_for_tests() -> None:
    global _backend, _backend_kind, _warned
    _backend = None
    _backend_kind = None
    _warned = False

"""Phase 9 hardening middleware for the ARGUS ml service.

Stack (outermost → innermost), wired in ``app/main.py`` ::

    CORS → RequestID → AuditLog → RateLimit → Auth (Supabase JWT) → router

Rate limiting runs *outside* auth so the anonymous bucket (60/min, per
docs/API_SPEC.md) applies to callers without credentials; the auth bucket
(600/min) is granted on credential *presentation* — requests with invalid
credentials still count against it and then receive 401 from the auth
layer. (Rationale: the limiter cannot know validity before the auth layer
runs; counting presented credentials in the higher bucket is the standard
trade-off and is documented here rather than hidden.)

- :class:`RequestIDMiddleware` — propagates / generates ``X-Request-ID``,
  adds ``X-Process-Time``.
- :class:`AuditLogMiddleware` — one JSONL line per request
  (method/path/status/latency_ms/user/request_id) to the ``argus.audit``
  logger (stdout); best-effort INSERT into the ``system_logs`` hypertable
  when ``ARGUS_DB_DSN`` is set. Never fails the request. ``/health`` is
  excluded to avoid log spam from health checks.
- :class:`AuthMiddleware` — verifies Supabase JWTs (HS256) from
  ``Authorization: Bearer`` against ``SUPABASE_JWT_SECRET``.
  ``AUTH_DISABLED=true`` skips verification for local dev and logs a loud
  warning (never use outside localhost). ``/health``, ``/docs``,
  ``/openapi.json``, ``/redoc`` stay public.
- :class:`RateLimitMiddleware` — sliding-window limiter backed by Redis
  (``REDIS_URL``) when configured, in-process otherwise (loud fallback).
  Buckets per docs/API_SPEC.md:
  60/min anonymous metadata · 600/min authenticated · 60/min heavy compute
  (POST /api/v1/predictions, /api/v1/backtests, /api/v1/portfolio/analyze,
  /api/v1/research). ``/health`` and the docs paths are exempt.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

log = logging.getLogger("argus.ml")
audit_log = logging.getLogger("argus.audit")

PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}

# (method, path-prefix) pairs classified as heavy compute per API_SPEC.md
HEAVY_ENDPOINTS = (
    ("POST", "/api/v1/predictions"),
    ("POST", "/api/v1/backtests"),
    ("POST", "/api/v1/portfolio/analyze"),
    ("POST", "/api/v1/research"),
)


def _error(code: str, message: str, status: int):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": None}},
    )


def _auth_disabled() -> bool:
    return os.environ.get("AUTH_DISABLED", "").lower() == "true"


# ---------------------------------------------------------------------------
# Request ID
# ---------------------------------------------------------------------------

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = f"{time.perf_counter() - start:.4f}s"
        return response


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditLogMiddleware(BaseHTTPMiddleware):
    """Audit trail: JSONL to stdout; system_logs row when a DB is configured."""

    _db_warned = False

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path == "/health":
            return await call_next(request)
        start = time.perf_counter()
        response = await call_next(request)
        latency_ms = (time.perf_counter() - start) * 1000.0
        user = getattr(request.state, "user", None)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "service": "ml",
            "level": "INFO",
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "latency_ms": round(latency_ms, 2),
            "user": (user or {}).get("sub") if isinstance(user, dict) else "anon",
            "request_id": getattr(request.state, "request_id", None),
        }
        audit_log.info(json.dumps(entry))
        self._write_system_log(entry)
        return response

    def _write_system_log(self, entry: dict) -> None:
        from app import db as _db
        if not _db.db_configured():
            return
        n = _db.execute(
            """INSERT INTO system_logs (time, level, service, message, context)
               VALUES (now(), %s, %s, %s, %s::jsonb)""",
            (entry["level"], entry["service"],
             f"{entry['method']} {entry['path']} {entry['status']}",
             json.dumps({k: entry[k] for k in
                         ("method", "path", "status", "latency_ms",
                          "user", "request_id")})))
        if n == 0 and not AuditLogMiddleware._db_warned:
            log.warning("system_logs write failed (stdout audit continues)")
            AuditLogMiddleware._db_warned = True


# ---------------------------------------------------------------------------
# Auth — Supabase JWT
# ---------------------------------------------------------------------------

class AuthMiddleware(BaseHTTPMiddleware):
    _disabled_warned = False

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/docs"):
            request.state.authenticated = False
            request.state.user = None
            return await call_next(request)

        if _auth_disabled():
            if not AuthMiddleware._disabled_warned:
                log.warning("AUTH_DISABLED=true — JWT verification is OFF. "
                            "Local development only; never expose this publicly.")
                AuthMiddleware._disabled_warned = True
            request.state.authenticated = True
            request.state.user = {"sub": "dev", "email": "dev@localhost"}
            return await call_next(request)

        secret = os.environ.get("SUPABASE_JWT_SECRET")
        if not secret:
            log.error("SUPABASE_JWT_SECRET is not set — failing closed")
            return _error("AUTH_NOT_CONFIGURED",
                          "Authentication is not configured on this server.", 503)

        auth = request.headers.get("Authorization", "")
        if not auth.lower().startswith("bearer "):
            return _error("UNAUTHORIZED",
                          "Missing Authorization: Bearer <token> header.", 401)
        token = auth[7:].strip()
        try:
            from jose import jwt as jose_jwt
            payload = jose_jwt.decode(token, secret, algorithms=["HS256"],
                                      options={"verify_aud": False})
        except Exception:  # noqa: BLE001 — any JWT failure is a 401
            return _error("UNAUTHORIZED", "Invalid or expired token.", 401)

        request.state.authenticated = True
        request.state.user = {"sub": payload.get("sub"),
                              "email": payload.get("email"),
                              "role": payload.get("role")}
        return await call_next(request)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter.

    Backend: Redis (``REDIS_URL``) via ``app.rate_limit`` when configured —
    a shared atomic sliding window across replicas. Falls back to the
    in-process window with a loud log when Redis is unset/unreachable
    (see ``app/rate_limit.py``).

    Buckets (requests per 60s window), per docs/API_SPEC.md: anonymous 60,
    authenticated 600, heavy-compute endpoints 60. The bucket is chosen
    before the auth layer runs: heavy endpoint → 60; an ``Authorization``
    header is present → 600; otherwise → 60 (anonymous). Keys are
    ``(bucket, client-ip)``. Requests with invalid credentials count
    against the 600 bucket and then receive 401 from the auth layer —
    documented trade-off, not a bypass (the limiter cannot verify
    credentials before auth runs).

    ``/health`` and the docs paths are exempt. Limits are
    constructor/env-overridable for tests (``RATE_LIMIT_ANON_PER_MIN``,
    ``RATE_LIMIT_AUTH_PER_MIN``, ``RATE_LIMIT_HEAVY_PER_MIN``).
    ``backend="auto"|"redis"|"memory"`` selects the limiter backend
    ("auto" = Redis when REDIS_URL works).
    """

    _no_redis_warned = False

    def __init__(self, app, anon_per_min: int | None = None,
                 auth_per_min: int | None = None,
                 heavy_per_min: int | None = None,
                 backend: str = "auto"):
        super().__init__(app)
        self.anon_per_min = anon_per_min or int(os.environ.get("RATE_LIMIT_ANON_PER_MIN", 60))
        self.auth_per_min = auth_per_min or int(os.environ.get("RATE_LIMIT_AUTH_PER_MIN", 600))
        self.heavy_per_min = heavy_per_min or int(os.environ.get("RATE_LIMIT_HEAVY_PER_MIN", 60))
        from app import rate_limit as _rl
        if backend == "redis":
            self._limiter = _rl.RedisSlidingWindow(os.environ["REDIS_URL"])
        elif backend == "memory":
            self._limiter = _rl.MemorySlidingWindow()
        else:  # auto: shared Redis when REDIS_URL works, else a
            # per-instance in-memory window (same semantics as the
            # pre-Redis limiter: one app per process).
            url = os.environ.get("REDIS_URL")
            if url:
                try:
                    self._limiter = _rl.RedisSlidingWindow(url)
                    log.info("rate limiter: Redis backend active")
                except Exception as exc:  # noqa: BLE001 — degrade loudly
                    log.warning("rate limiter: Redis unreachable (%s) — "
                                "in-process limiter (per-replica only)", exc)
                    self._limiter = _rl.MemorySlidingWindow()
            else:
                if not RateLimitMiddleware._no_redis_warned:
                    log.warning("rate limiter: REDIS_URL not set — "
                                "in-process limiter. Multi-replica deploys "
                                "need REDIS_URL for a shared limit.")
                    RateLimitMiddleware._no_redis_warned = True
                self._limiter = _rl.MemorySlidingWindow()

    def _bucket(self, request: Request) -> tuple[str, int]:
        for method, prefix in HEAVY_ENDPOINTS:
            if request.method == method and request.url.path.startswith(prefix):
                return "heavy", self.heavy_per_min
        if request.headers.get("Authorization"):
            return "auth", self.auth_per_min
        return "anon", self.anon_per_min

    def _identity(self, request: Request) -> str:
        client = request.client.host if request.client else "unknown"
        return f"ip:{client}"

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/docs"):
            return await call_next(request)
        bucket, limit = self._bucket(request)
        allowed, remaining, retry_after = self._limiter.check(
            bucket, self._identity(request), limit)
        if not allowed:
            resp = _error("RATE_LIMITED",
                          f"Rate limit exceeded: {limit} requests/min ({bucket}).", 429)
            resp.headers["Retry-After"] = str(retry_after)
            resp.headers["X-RateLimit-Limit"] = str(limit)
            resp.headers["X-RateLimit-Remaining"] = "0"
            return resp
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response

"""Phase 9 hardening middleware for the ARGUS ml service.

Stack (outermost → innermost), wired in ``app/main.py`` ::

    CORS → RequestID → AuditLog → RateLimit → Auth (Supabase JWT) → router

Rate limiting still runs *outside* auth (so the anonymous bucket applies
before credentials are verified), but the bucket is now derived from a
**verified** identity: ``RateLimitMiddleware`` asks ``app.authz`` to verify
the bearer token itself (the result is cached on the request scope and
reused by ``AuthMiddleware``). The mere presence of an ``Authorization``
header no longer buys the 600/min bucket — an unverifiable token is
counted against the anonymous 60/min bucket and then rejected with 401 by
the auth layer. Verified callers are keyed per subject (``user:<sub>``),
anonymous callers per client IP.

Security/reliability invariants enforced here:

* **Fail closed on AUTH_DISABLED** — :func:`assert_auth_configuration` is
  wired into the ASGI **startup** hook in ``app/main.py`` (NOT at import
  time, so importing the app stays safe for test runners/tooling). A
  process started with ``AUTH_DISABLED=true`` and no explicit local/dev
  opt-in raises at startup instead of serving anonymous traffic. The opt-in
  is ``ARGUS_ENV`` in ``{development,dev,local,test,testing}`` or
  ``ARGUS_ALLOW_AUTH_DISABLED`` in ``{1,true,yes,on}``. render.yaml sets
  ``AUTH_DISABLED=true`` with no opt-in, so a production deploy of that
  file refuses to boot.
* **Identity** — :class:`AuthMiddleware` verifies HS256 Supabase JWTs with
  audience/issuer validation when ``SUPABASE_JWT_AUD`` / ``SUPABASE_JWT_ISS``
  are configured (``app.authz.decode_token``); routers read the verified
  subject through ``app.authz`` (``require_subject`` / ``ensure_owner``)
  instead of hardcoding ``user_id = "local"``.
* **Audit-log integrity** — the client-supplied ``X-Request-ID`` is
  validated (charset + length) and replaced with a fresh id otherwise, so
  it can never be reflected into response headers or logs verbatim.
* **Proxy-safe identity** — ``X-Forwarded-For`` is honoured only when the
  direct peer is listed in ``TRUSTED_PROXIES`` (comma-separated IPs/CIDRs);
  the default (empty) ignores the header entirely, so a spoofed XFF cannot
  move a caller into someone else's bucket.
* **Non-blocking audit writes** — the ``system_logs`` INSERT runs in a
  worker thread (``anyio.to_thread``) and can never fail or stall the
  event loop / the response.
* **Safe-by-construction CORS** — :func:`cors_settings` never combines
  ``allow_credentials=True`` with wildcard origins/methods/headers and is
  import-safe (an empty allowlist when unconfigured outside dev mode);
  :func:`assert_cors_configuration` is wired into the same ASGI startup
  hook and refuses to serve when ``CORS_ORIGINS`` is unset or ``*`` outside
  explicit dev mode. ``app/main.py`` must pass ``cors_settings()`` to
  ``CORSMiddleware`` (see ``changes/C-security-reliability.md``).

Middleware classes:

- :class:`RequestIDMiddleware` — propagates / generates ``X-Request-ID``,
  adds ``X-Process-Time``.
- :class:`AuditLogMiddleware` — one JSONL line per request
  (method/path/status/latency_ms/user/client/request_id) to the
  ``argus.audit`` logger (stdout); best-effort INSERT into the
  ``system_logs`` hypertable when a DB is configured, in a worker thread.
  Never fails the request. ``/health`` is excluded to avoid log spam.
- :class:`AuthMiddleware` — verifies Supabase JWTs (HS256).
  ``AUTH_DISABLED=true`` skips verification for local dev *only when the
  explicit dev opt-in is present* (see above) and logs a loud warning.
  ``/health``, ``/docs``, ``/openapi.json``, ``/redoc`` stay public.
- :class:`RateLimitMiddleware` — sliding-window limiter backed by Redis
  (``REDIS_URL``) when configured, in-process otherwise (loud fallback).
  Buckets per docs/API_SPEC.md: 60/min anonymous metadata · 600/min
  *verified* callers · 60/min heavy compute (POST /api/v1/predictions,
  /api/v1/backtests, /api/v1/portfolio/analyze, /api/v1/research).
  ``/health`` and the docs paths are exempt.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import anyio
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app import authz

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
    """Backwards-compatible alias for :func:`app.authz.auth_disabled`."""
    return authz.auth_disabled()


# ---------------------------------------------------------------------------
# Item 1 — fail closed on AUTH_DISABLED without an explicit dev opt-in
# ---------------------------------------------------------------------------

def assert_auth_configuration() -> None:
    """Refuse to serve when AUTH_DISABLED is on without a local/dev opt-in.

    AUTH_DISABLED=true makes every request authenticated as a fixed dev
    identity. That is only acceptable in a process that *explicitly*
    declares itself local/dev. This function is called from the ASGI
    **startup** hook wired in ``app/main.py``, so a real server
    (``uvicorn app.main:app``) raises loudly at startup and never serves
    unauthenticated traffic.

    It is deliberately NOT called at import time: importing this module must
    stay side-effect free so test runners, linters and doc generators can
    import the app with any environment. Fail-closed belongs at startup.
    """
    if not authz.auth_disabled():
        return
    source = authz.dev_optin_source()
    if source:
        log.warning(
            "AUTH_DISABLED=true honoured because %s explicitly marks this "
            "process as local/dev — JWT verification is OFF. Never use this "
            "configuration in production.", source)
        return
    raise RuntimeError(
        "FATAL: AUTH_DISABLED=true but no explicit local/dev opt-in is set. "
        "Refusing to start: every request would be served as the anonymous "
        "'dev' identity. Set ARGUS_ENV=development (or "
        "ARGUS_ALLOW_AUTH_DISABLED=1) ONLY for local development, or remove "
        "AUTH_DISABLED from the production environment "
        "(render.yaml currently sets AUTH_DISABLED=\"true\").")


# ---------------------------------------------------------------------------
# Request ID (item 9) — validate, never reflect client input verbatim
# ---------------------------------------------------------------------------

# Bounded charset (no CR/LF/space/quotes) and bounded length: a request id
# reaches response headers and log lines, so it must not be able to inject
# either.
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_REQUEST_ID_LEN = 128


def sanitize_request_id(raw: Optional[str]) -> str:
    """Return a safe request id: the client value when valid, else a fresh one."""
    if raw:
        candidate = raw.strip()
        if len(candidate) <= _MAX_REQUEST_ID_LEN and _REQUEST_ID_RE.fullmatch(candidate):
            return candidate
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Client identity (item 4) — X-Forwarded-For only from trusted proxies
# ---------------------------------------------------------------------------

_TRUSTED_CACHE: Optional[Tuple[str, List[Any]]] = None


def trusted_networks() -> List[Any]:
    """Parsed ``TRUSTED_PROXIES`` (comma-separated IPs/CIDRs), memoised."""
    global _TRUSTED_CACHE
    raw = os.environ.get("TRUSTED_PROXIES", "")
    if _TRUSTED_CACHE is not None and _TRUSTED_CACHE[0] == raw:
        return _TRUSTED_CACHE[1]
    nets: List[Any] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            log.warning("TRUSTED_PROXIES: ignoring invalid entry %r", part)
    _TRUSTED_CACHE = (raw, nets)
    return nets


def is_trusted_proxy(host: Optional[str]) -> bool:
    """True when ``host`` is an address inside the configured proxy allowlist."""
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(addr.version == net.version and addr in net
               for net in trusted_networks())


def forwarded_chain(request: Request) -> List[str]:
    """Parse ``X-Forwarded-For`` into a list of syntactically valid IPs."""
    raw = request.headers.get("X-Forwarded-For", "")
    chain: List[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ipaddress.ip_address(part)
        except ValueError:
            continue  # garbage hop: drop it rather than trusting it
        chain.append(part)
    return chain


def client_identity(request: Request) -> str:
    """Best-effort real client address, safe against XFF spoofing.

    The header is only consulted when the direct peer (``request.client``)
    is in ``TRUSTED_PROXIES``. Walking the chain right-to-left returns the
    first hop that is not itself a trusted proxy (the standard algorithm);
    if the whole chain is trusted, the left-most hop is returned. With
    ``TRUSTED_PROXIES`` unset the direct peer is always used — the same
    behaviour as before, but now the spoofable header is ignored by default.
    """
    peer = request.client.host if request.client and request.client.host else "unknown"
    if not is_trusted_proxy(peer):
        return peer
    chain = forwarded_chain(request)
    if not chain:
        return peer
    for hop in reversed(chain):
        if not is_trusted_proxy(hop):
            return hop
    return chain[0]


# ---------------------------------------------------------------------------
# Request ID
# ---------------------------------------------------------------------------

class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = sanitize_request_id(request.headers.get("X-Request-ID"))
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
    """Audit trail: JSONL to stdout; system_logs row when a DB is configured.

    The ``system_logs`` INSERT is blocking (psycopg); it runs in a worker
    thread via ``anyio.to_thread`` so a slow database cannot block the
    event loop, and any failure is swallowed (stdout audit continues).
    """

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
            "client": client_identity(request),
            "request_id": getattr(request.state, "request_id", None),
        }
        audit_log.info(json.dumps(entry))
        await self._write_system_log_async(entry)
        return response

    # -- non-blocking wrapper -------------------------------------------------
    def _db_configured(self) -> bool:
        try:
            from app import db as _db
            return bool(_db.db_configured())
        except Exception:  # noqa: BLE001 — a broken import must not break serving
            return False

    @classmethod
    def _warn_db_once(cls, message: str) -> None:
        if not cls._db_warned:
            log.warning("%s (stdout audit continues)", message)
            cls._db_warned = True

    async def _write_system_log_async(self, entry: dict) -> None:
        if not self._db_configured():
            return
        try:
            # Worker thread: the loop stays free for other requests.
            await anyio.to_thread.run_sync(self._write_system_log, entry)
        except Exception as exc:  # noqa: BLE001
            self._warn_db_once(f"system_logs write failed: {exc}")

    def _write_system_log(self, entry: dict) -> None:
        """Blocking INSERT — only ever called from a worker thread."""
        try:
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
                              "user", "client", "request_id")})))
            if n == 0:
                self._warn_db_once("system_logs write failed")
        except Exception as exc:  # noqa: BLE001
            self._warn_db_once(f"system_logs write error: {exc}")


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

        if authz.auth_disabled() and authz.dev_mode_explicit():
            if not AuthMiddleware._disabled_warned:
                log.warning("AUTH_DISABLED=true — JWT verification is OFF. "
                            "Local development only; never expose this publicly.")
                AuthMiddleware._disabled_warned = True
            claims: Dict[str, Any] = dict(authz.DEV_CLAIMS)
        else:
            claims = None
            token_claims, reason = authz.token_status(request)
            if token_claims is not None:
                claims = token_claims
                if authz.jwt_audience() is None:
                    authz.warn_audience_unset()
            elif reason == "not_configured":
                log.error("SUPABASE_JWT_SECRET is not set — failing closed")
                return _error("AUTH_NOT_CONFIGURED",
                              "Authentication is not configured on this server.", 503)
            else:
                return _error("UNAUTHORIZED",
                              "Missing or invalid Authorization: Bearer <token> header.", 401)

        request.state.authenticated = True  # noqa: SIM113 — dev bypass or verified token
        request.state.user = {"sub": claims.get("sub"),
                              "email": claims.get("email"),
                              "role": claims.get("role")}
        return await call_next(request)


# ---------------------------------------------------------------------------
# CORS (item 8) — safe-by-construction policy for app/main.py
# ---------------------------------------------------------------------------

_DEFAULT_DEV_ORIGINS = ("http://localhost:3000",)
_DEFAULT_METHODS = ("GET", "POST", "DELETE", "OPTIONS")
_DEFAULT_HEADERS = ("Authorization", "Content-Type", "X-Request-ID")


@dataclass(frozen=True)
class CorsSettings:
    """Explicit, credential-safe CORS policy (no wildcards)."""

    origins: List[str] = field(default_factory=list)
    allow_methods: List[str] = field(default_factory=list)
    allow_headers: List[str] = field(default_factory=list)


def _env_list(name: str, default: Tuple[str, ...]) -> List[str]:
    raw = os.environ.get(name, "")
    values = [v.strip() for v in raw.split(",") if v.strip()]
    return values or list(default)


def cors_settings() -> CorsSettings:
    """Build the CORS policy. Never raises — safe to call at import time.

    * ``CORS_ORIGINS`` is honoured as an explicit allowlist; a ``*`` entry is
      dropped (``allow_credentials=True`` forbids wildcard origins).
    * With nothing configured the allowlist stays EMPTY (fail closed: no
      browser origin is allowed) unless the process is in explicit dev mode,
      where it falls back to ``http://localhost:3000`` for compatibility.
    * Wildcard methods/headers are never emitted while credentials are
      allowed; ``*`` entries are dropped.

    Startup enforcement (raise when misconfigured, outside dev) lives in
    :func:`assert_cors_configuration`, wired into the ASGI lifespan.
    """
    raw = os.environ.get("CORS_ORIGINS", "")
    origins = [o.strip() for o in raw.split(",") if o.strip()]
    if "*" in origins:
        log.warning("CORS_ORIGINS contains '*' — ignored: "
                    "allow_credentials=True forbids wildcard origins.")
        origins = [o for o in origins if o != "*"]
    if not origins:
        if authz.dev_mode_explicit():
            log.warning("CORS_ORIGINS is not set — defaulting to the local dev "
                        "origin %s", list(_DEFAULT_DEV_ORIGINS))
            origins = list(_DEFAULT_DEV_ORIGINS)
        else:
            log.error("CORS_ORIGINS is not set and this is not an explicit "
                      "dev process — the CORS allowlist is EMPTY (fail closed: "
                      "no cross-origin browser request will be accepted).")
    methods = [m for m in _env_list("CORS_ALLOW_METHODS", _DEFAULT_METHODS)
               if m != "*"]
    headers = [h for h in _env_list("CORS_ALLOW_HEADERS", _DEFAULT_HEADERS)
               if h != "*"]
    if not methods:
        methods = list(_DEFAULT_METHODS)
    if not headers:
        headers = list(_DEFAULT_HEADERS)
    return CorsSettings(origins=origins, allow_methods=methods, allow_headers=headers)


def assert_cors_configuration() -> None:
    """Startup guard: refuse to serve with an unset/wildcard CORS policy.

    Called from the ASGI lifespan (``app/main.py``) together with
    :func:`assert_auth_configuration`; NOT at import time, so importing the
    app stays safe for tooling and tests.
    """
    raw = os.environ.get("CORS_ORIGINS", "")
    if "*" in [o.strip() for o in raw.split(",")]:
        raise RuntimeError(
            "CORS_ORIGINS='*' is not allowed while allow_credentials=True. "
            "List the exact web origins that may call this API.")
    if not cors_settings().origins:
        raise RuntimeError(
            "CORS_ORIGINS must be set to an explicit origin allowlist "
            "outside local/dev mode. Set CORS_ORIGINS in the production "
            "environment (set ARGUS_ENV=development for local work).")


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding-window rate limiter.

    Backend: Redis (``REDIS_URL``) via ``app.rate_limit`` when configured —
    a shared atomic sliding window across replicas. Degrades to the
    in-process window with a loud log on any Redis error (see
    ``app/rate_limit.py``); a Redis hiccup never 500s the API.

    Buckets (requests per 60s window), per docs/API_SPEC.md: anonymous 60,
    verified 600, heavy-compute endpoints 60. The bucket is derived from a
    VERIFIED identity (``app.authz.token_identity``, i.e. a token whose
    signature/expiry/audience actually checked out) — an ``Authorization``
    header that does not verify gets the anonymous bucket and then a 401
    from the auth layer. Verified callers are keyed ``user:<sub>``,
    anonymous callers ``ip:<client>`` where the client address only honours
    ``X-Forwarded-For`` from a trusted proxy (``TRUSTED_PROXIES``).

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

    def _bucket(self, request: Request) -> Tuple[str, int]:
        for method, prefix in HEAVY_ENDPOINTS:
            if request.method == method and request.url.path.startswith(prefix):
                return "heavy", self.heavy_per_min
        # Verified identity only: header presence never escalates the bucket.
        if authz.token_identity(request) is not None:
            return "auth", self.auth_per_min
        return "anon", self.anon_per_min

    def _identity(self, request: Request) -> str:
        claims = authz.token_identity(request)
        if claims:
            return f"user:{claims.get('sub')}"
        return f"ip:{client_identity(request)}"

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/docs"):
            return await call_next(request)
        bucket, limit = self._bucket(request)
        try:
            allowed, remaining, retry_after = self._limiter.check(
                bucket, self._identity(request), limit)
        except Exception as exc:  # noqa: BLE001 — contract is "never raise"
            # Fail closed (503), never fail open, if a backend breaks its
            # no-raise contract.
            log.error("rate limiter backend raised (%s) — failing closed", exc)
            return _error("RATE_LIMIT_UNAVAILABLE",
                          "Rate limiter unavailable; retry shortly.", 503)
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

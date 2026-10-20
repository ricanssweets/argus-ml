"""ARGUS ML service — FastAPI app.

Base path /api/v1 per API_SPEC.md. Every response uses the envelope
{data, meta{as_of, data_status, request_id}}; errors use
{error{code, message, details}}.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, Dict

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import data_stub, db
from app.middleware import (
    AuditLogMiddleware,
    AuthMiddleware,
    RateLimitMiddleware,
    RequestIDMiddleware,
    assert_auth_configuration,
    assert_cors_configuration,
    cors_settings,
)
from app.quant_compat import QUANT_AVAILABLE
from app.routers import alerts, assets, backtests, etfs, models, portfolio, predictions, research, signals

log = logging.getLogger("argus.ml")
logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Fail closed at STARTUP, never at import time.

    Importing this module (test runners, linters, doc generators) must stay
    safe; actually serving traffic must not. ``assert_auth_configuration``
    refuses to boot when AUTH_DISABLED is on without an explicit local/dev
    opt-in, and ``assert_cors_configuration`` refuses a wildcard/unset
    allowlist outside dev — both raise here, so the container exits loudly
    instead of silently serving unauthenticated or over-permissive traffic.
    """
    assert_auth_configuration()
    assert_cors_configuration()
    yield
    db.close_pool()


_cors = cors_settings()  # import-safe: empty allowlist when unset outside dev

app = FastAPI(
    title="ARGUS ML Service",
    version="0.1.0",
    description=(
        "Institutional AI stock & ETF prediction platform — ML service. "
        "Ensemble predictor with Postgres persistence and calibration maps."
    ),
    lifespan=lifespan,
)


# Middleware stack — add_middleware inserts outermost-last, so the execution
# order (outer → inner) is: CORS → RequestID → AuditLog → RateLimit → Auth.
# Rate limiting sits outside auth so the anonymous bucket (60/min) applies
# before credentials are verified (see app/middleware.py for the rationale).
# /health and /docs stay public (auth-exempt); /health is also exempt from
# rate limiting and audit logging so health checks never 429 or spam logs.
app.add_middleware(AuthMiddleware)  # innermost
app.add_middleware(RateLimitMiddleware)
app.add_middleware(AuditLogMiddleware)
app.add_middleware(RequestIDMiddleware)
app.add_middleware(
    CORSMiddleware,  # outermost: preflight handled before auth
    allow_origins=_cors.origins,
    allow_credentials=True,
    allow_methods=_cors.allow_methods,
    allow_headers=_cors.allow_headers,
)


def _error(code: str, message: str, details=None, status: int = 500):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


# Every framework-raised HTTP status maps to a named error code so the
# envelope is coherent across routes (a 405 used to fall through to the
# generic "HTTP_ERROR" while 404/401/403/429 had names). Codes are stable
# API contract — clients switch on them, never on the prose message.
_HTTP_ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
    504: "GATEWAY_TIMEOUT",
}


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    code = _HTTP_ERROR_CODES.get(exc.status_code,
                                 f"HTTP_{exc.status_code}")  # never generic
    return _error(code, str(exc.detail), None, exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return _error("VALIDATION_ERROR", "Request validation failed",
                  {"errors": exc.errors()}, 422)


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    log.exception("unhandled error")
    return _error("INTERNAL_ERROR", "Internal server error", None, 500)


def _db_reachable() -> bool:
    """Bounded, direct round trip — never waits on the psycopg pool.

    Two problems with probing through ``db.fetchall``: pool acquisition can
    block for the full pool timeout (measured 7.6 s with the database
    unreachable, which would blow Render's health-check deadline), and
    ``db_configured()`` only checks that DATABASE_URL is SET — so the old
    /health reported ``data_layer: "postgres"`` even while Postgres was down.
    ``migrate.probe_database`` opens its own connection with an explicit
    timeout and never raises.
    """
    if not db.db_configured():
        return False
    try:
        from app.migrate import probe_database

        return bool(probe_database(
            timeout=float(os.environ.get("ARGUS_HEALTH_DB_TIMEOUT", "1.5"))))
    except Exception:  # noqa: BLE001 — health must never raise
        return False


@app.get("/health", tags=["ops"])
def health():
    """Liveness + honest data-layer status.

    Always returns 200 so a database outage cannot make the platform's
    health check fail (and restart-loop the container); the payload states
    the degradation explicitly instead of claiming health it does not have.
    """
    configured = db.db_configured()
    reachable = _db_reachable() if configured else False
    if not configured:
        data_layer = "stub (UNCONFIRMED demo)"
    elif reachable:
        data_layer = "postgres"
    else:
        data_layer = "postgres (UNREACHABLE)"

    payload: Dict[str, Any] = {
        "status": "ok" if (not configured or reachable) else "degraded",
        "service": "argus-ml",
        "version": "0.1.0",
        "quant_available": QUANT_AVAILABLE,
        "data_layer": data_layer,
        "db_configured": configured,
        "db_reachable": reachable,
        "as_of": data_stub.utcnow_iso(),
    }
    return payload


app.include_router(assets.router, prefix="/api/v1")
app.include_router(predictions.router, prefix="/api/v1")
app.include_router(signals.router, prefix="/api/v1")
app.include_router(etfs.router, prefix="/api/v1")
app.include_router(portfolio.router, prefix="/api/v1")
app.include_router(backtests.router, prefix="/api/v1")
app.include_router(models.router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")

"""ARGUS ML service — FastAPI app.

Base path /api/v1 per API_SPEC.md. Every response uses the envelope
{data, meta{as_of, data_status, request_id}}; errors use
{error{code, message, details}}.
"""

from __future__ import annotations

import logging
import os

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
)
from app.quant_compat import QUANT_AVAILABLE
from app.routers import alerts, assets, backtests, etfs, models, portfolio, predictions, research, signals

log = logging.getLogger("argus.ml")
logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="ARGUS ML Service",
    version="0.1.0",
    description=(
        "Institutional AI stock & ETF prediction platform — ML service. "
        "Ensemble predictor with Postgres persistence and calibration maps."
    ),
)

def _cors_origins() -> list[str]:
    raw = os.environ.get("CORS_ORIGINS", "http://localhost:3000")
    return [o.strip() for o in raw.split(",") if o.strip()]


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
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error(code: str, message: str, details=None, status: int = 500):
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "details": details}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    code = {404: "NOT_FOUND", 401: "UNAUTHORIZED", 403: "FORBIDDEN",
            429: "RATE_LIMITED"}.get(exc.status_code, "HTTP_ERROR")
    return _error(code, str(exc.detail), None, exc.status_code)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return _error("VALIDATION_ERROR", "Request validation failed",
                  {"errors": exc.errors()}, 422)


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    log.exception("unhandled error")
    return _error("INTERNAL_ERROR", "Internal server error", None, 500)


@app.get("/health", tags=["ops"])
def health():
    return {
        "status": "ok",
        "service": "argus-ml",
        "version": "0.1.0",
        "quant_available": QUANT_AVAILABLE,
        "data_layer": "postgres" if db.db_configured() else "stub (UNCONFIRMED demo)",
        "as_of": data_stub.utcnow_iso(),
    }


app.include_router(assets.router, prefix="/api/v1")
app.include_router(predictions.router, prefix="/api/v1")
app.include_router(signals.router, prefix="/api/v1")
app.include_router(etfs.router, prefix="/api/v1")
app.include_router(portfolio.router, prefix="/api/v1")
app.include_router(backtests.router, prefix="/api/v1")
app.include_router(models.router, prefix="/api/v1")
app.include_router(research.router, prefix="/api/v1")
app.include_router(alerts.router, prefix="/api/v1")

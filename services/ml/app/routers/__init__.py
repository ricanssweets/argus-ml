"""API routers for /api/v1. Shared envelope helper lives here."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app import data_stub


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def env(data: Any, data_status: str, request: Request, as_of: str | None = None):
    """Build the API_SPEC envelope {data, meta{as_of, data_status, request_id}}."""
    return {
        "data": data,
        "meta": {
            "as_of": as_of or data_stub.utcnow_iso(),
            "data_status": data_status,
            "request_id": request_id(request),
        },
    }


def not_found(detail: str):
    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail=detail)

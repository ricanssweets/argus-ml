"""API routers for /api/v1. Shared envelope helper lives here."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app import data_stub


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


# Envelope data_status aggregation -------------------------------------------
# Identical ranking to app.predict.STATUS_RANK. SYNTHETIC_FIXTURE ranks with
# UNCONFIRMED: a labeled fixture proves nothing about the real world.
_STATUS_RANK = {
    "CONFIRMED": 0,
    "LIKELY": 1,
    "UNCONFIRMED": 2,
    "SYNTHETIC_FIXTURE": 2,
    "CONFLICTING": 3,
    "MISSING": 4,
}


def aggregate_status(statuses, default: str = "UNCONFIRMED") -> str:
    """Worst-of-items ``meta.data_status`` for a multi-item envelope.

    An envelope may never look *better* than the records it carries, so the
    meta reports the most pessimistic status among its items (first-seen
    wins on ties: CONFIRMED == CONFIRMED only). Empty/unknown input returns
    ``default``. This is what fixes items reporting CONFIRMED while the
    envelope claimed UNCONFIRMED, and vice versa.
    """
    best_rank = -1
    worst: str | None = None
    for s in statuses:
        if not isinstance(s, str):
            continue
        rank = _STATUS_RANK.get(s)
        if rank is None or rank <= best_rank:
            continue
        best_rank, worst = rank, s
    return worst or default


def env(data: Any, data_status: str, request: Request, as_of: str | None = None,
        extra_meta: dict | None = None):
    """Build the API_SPEC envelope {data, meta{as_of, data_status, request_id}}.

    ``extra_meta`` adds documented, additive keys (e.g. the applied filters)
    without changing the three keys apps/web depends on.
    """
    meta = {
        "as_of": as_of or data_stub.utcnow_iso(),
        "data_status": data_status,
        "request_id": request_id(request),
    }
    if extra_meta:
        meta.update(extra_meta)
    return {"data": data, "meta": meta}


def not_found(detail: str):
    from fastapi import HTTPException

    raise HTTPException(status_code=404, detail=detail)

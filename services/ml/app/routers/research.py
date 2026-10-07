"""POST /api/v1/research — research assistant (ARGUS Phase 6).

Deterministic query planner (app.research_planner): a keyword/intent
router maps the six product-brief questions to SELECT queries over the
research store (SQLite mirror of the migrations' predictions / observatory
tables until Postgres is wired).

Hard rule (API_SPEC.md): every numeric claim cites a stored record
[{table, id, as_of}]; anything without a stored record gets
"I don't have data for that." — numbers are never invented.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app import research_planner
from app.routers import env
from app.schemas import DISCLAIMER, Citation, ResearchRequest, ResearchResponse

router = APIRouter(tags=["research"])


@router.post("/research")
def research(body: ResearchRequest, request: Request):
    res = research_planner.answer(body.question)
    payload = ResearchResponse(
        answer=res.answer,
        citations=[Citation(**c) for c in res.citations],
        sql_used=res.sql_used,
        data_status=res.data_status,  # type: ignore[arg-type]
        disclaimer=DISCLAIMER,
    )
    return env(payload.model_dump(), res.data_status, request)

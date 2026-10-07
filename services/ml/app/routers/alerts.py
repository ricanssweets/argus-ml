"""Alerts — ARGUS Phase 7 (real wiring).

Backed by SQLite (services/ml/data/alerts.db, tables mirroring
db/migrations/005_portfolio_signals.sql: alert_rules, alerts) until
Postgres is wired. Override with the ARGUS_ALERTS_DB env var (tests).

Endpoints:
  GET    /api/v1/alerts                 — list fired alerts (IN_APP delivery)
  POST   /api/v1/alert-rules            — create a rule
  GET    /api/v1/alert-rules            — list rules
  DELETE /api/v1/alert-rules/{id}       — delete a rule
  POST   /api/v1/alerts/evaluate        — evaluate rule(s) against price bars
                                          (argus_quant.alerts) and persist
                                          fired alerts
  POST   /api/v1/alerts/{id}/deliver    — push/email delivery: explicit STUBS,
                                          always {"status": "not_configured"}

Rule evaluation never duplicates quant logic: it imports
argus_quant.alerts via app.quant_compat and returns 503 (honest) when the
quant package is unavailable.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app import data_stub, delivery
from app import db as _pg
from app.quant_compat import QUANT_AVAILABLE, quant_attr
from app.routers import env, not_found
from app.schemas import Alert, AlertRule, AlertRuleCreate

router = APIRouter(tags=["alerts"])
log = logging.getLogger("argus.ml.alerts")

# Storage backend: Postgres svc mirror tables when DATABASE_URL is set,
# else the SQLite sidecar. Evaluated once at import (env at startup).
_USE_PG = _pg.db_configured()
if _USE_PG:
    _pg.ensure_mirror()


def _svc(sql: str) -> str:
    """Rewrite unqualified mirror table names to the svc schema (PG)."""
    import re
    sql = re.sub(r"\balert_rules\b", "svc.alert_rules", sql)
    sql = re.sub(r"\balerts\b", "svc.alerts", sql)
    return sql


def _all(sql: str, params: tuple = ()):
    if _USE_PG:
        return _pg.fetchall(_svc(sql), params)
    con = _connect()
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def _one(sql: str, params: tuple = ()):
    rows = _all(sql, params)
    return rows[0] if rows else None


def _write(sql: str, params: tuple = ()) -> int:
    if _USE_PG:
        return _pg.execute(_svc(sql), params)
    con = _connect()
    try:
        cur = con.execute(sql, params)
        con.commit()
        return cur.rowcount
    finally:
        con.close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_rules (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL DEFAULT 'local',
    rule_type  TEXT NOT NULL,
    asset_id   TEXT,
    ticker     TEXT,
    params     TEXT NOT NULL DEFAULT '{}',
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL DEFAULT 'local',
    created_at   TEXT NOT NULL,
    alert_type   TEXT NOT NULL,
    asset_id     TEXT,
    ticker       TEXT,
    title        TEXT NOT NULL,
    body         TEXT,
    severity     TEXT NOT NULL DEFAULT 'INFO',
    channel      TEXT NOT NULL DEFAULT 'IN_APP',
    delivered_at TEXT,
    read_at      TEXT,
    rule_id      TEXT
);
"""


def db_path() -> str:
    e = os.environ.get("ARGUS_ALERTS_DB")
    if e:
        return e
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "..", "data", "alerts.db")


def _connect() -> sqlite3.Connection:
    p = db_path()
    os.makedirs(os.path.dirname(os.path.abspath(p)), exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row_to_rule(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": r["id"], "rule_type": r["rule_type"], "ticker": r["ticker"],
        "params": json.loads(r["params"] or "{}"),
        "is_active": bool(r["is_active"]), "data_status": "UNCONFIRMED",
    }


def _row_to_alert(r: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": r["id"], "created_at": r["created_at"],
        "alert_type": r["alert_type"], "ticker": r["ticker"],
        "title": r["title"], "body": r["body"] or "",
        "severity": r["severity"], "channel": r["channel"],
        "read_at": r["read_at"],
    }


@router.get("/alerts")
def list_alerts(request: Request, alert_type: str = "",
                ticker: str = "", limit: int = 50):
    q = "SELECT * FROM alerts WHERE 1=1"
    params: List[Any] = []
    if alert_type:
        q += " AND alert_type = ?"
        params.append(alert_type)
    if ticker:
        q += " AND ticker = ?"
        params.append(ticker.upper())
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(limit, 200)))
    data = [_row_to_alert(r) for r in _all(q, tuple(params))]
    return env(data, "UNCONFIRMED", request)


@router.post("/alert-rules", status_code=201)
def create_alert_rule(body: AlertRuleCreate, request: Request):
    rule_id = uuid.uuid4().hex[:12]
    _write(
        "INSERT INTO alert_rules (id, user_id, rule_type, ticker, params,"
        " is_active, created_at) VALUES (?,?,?,?,?,?,?)",
        (rule_id, "local", body.rule_type,
         body.ticker.upper() if body.ticker else None,
         json.dumps(body.params or {}), 1, _utcnow()))
    row = _one("SELECT * FROM alert_rules WHERE id = ?", (rule_id,))
    data = _row_to_rule(row)
    return env(data, "UNCONFIRMED", request)


@router.get("/alert-rules")
def list_alert_rules(request: Request):
    rows = _all("SELECT * FROM alert_rules ORDER BY created_at DESC")
    data = [_row_to_rule(r) for r in rows]
    return env(data, "UNCONFIRMED", request)


@router.delete("/alert-rules/{rule_id}", status_code=200)
def delete_alert_rule(rule_id: str, request: Request):
    n = _write("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
    if n == 0:
        not_found(f"alert rule {rule_id} not found")
    return env({"deleted": rule_id}, "UNCONFIRMED", request)


class Bar(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None


class EvaluateRequest(BaseModel):
    rule_id: Optional[str] = None  # None = all active rules
    ticker: Optional[str] = None
    bars: Optional[List[Bar]] = None  # None = synthetic stub prices
    context: Dict[str, Any] = Field(default_factory=dict)


def _bars_for(ticker: str, bars: Optional[List[Bar]]) -> tuple:
    """Return (DataFrame, data_status, note). Falls back to synthetic stub
    prices when no bars are supplied — clearly labeled UNCONFIRMED."""
    if bars:
        df = pd.DataFrame([b.model_dump() for b in bars])
        return df, "UNCONFIRMED", "caller-supplied bars"
    synth = data_stub.synthetic_prices(ticker, 120)
    df = pd.DataFrame(synth)
    return df, "UNCONFIRMED", "synthetic stub prices (not real market data)"


@router.post("/alerts/evaluate")
def evaluate_alerts(body: EvaluateRequest, request: Request):
    evaluate_rule = quant_attr("alerts.evaluate_rule")
    if not QUANT_AVAILABLE or evaluate_rule is None:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail="argus_quant not available; rule evaluation unavailable "
                   "in this build (no quant logic is duplicated here).")
    if body.rule_id:
        rows = _all(
            "SELECT * FROM alert_rules WHERE id = ? AND is_active = 1",
            (body.rule_id,))
        if not rows:
            not_found(f"alert rule {body.rule_id} not found")
    else:
        rows = _all("SELECT * FROM alert_rules WHERE is_active = 1")
    rules = [_row_to_rule(r) for r in rows]

    fired: List[Dict[str, Any]] = []
    per_rule_status: List[Dict[str, Any]] = []
    for r in rules:
        ticker = (body.ticker or r["ticker"] or "NVDA").upper()
        df, _ds, note = _bars_for(ticker, body.bars)
        as_of = str(df["time"].iloc[-1]) if len(df) else _utcnow()
        context = {
            "as_of": as_of, "ticker": ticker, "bars": df,
            "prev_pred": body.context.get("prev_pred"),
            "curr_pred": body.context.get("curr_pred"),
            "options_unusual": bool(body.context.get("options_unusual")),
            "earnings_date": body.context.get("earnings_date"),
            "sentiment_velocity": body.context.get("sentiment_velocity"),
            "regime_prev": body.context.get("regime_prev"),
            "regime_curr": body.context.get("regime_curr"),
        }
        alert = evaluate_rule(r, context)
        per_rule_status.append(
            {"rule_id": r["id"], "rule_type": r["rule_type"],
             "ticker": ticker, "fired": alert is not None,
             "bars_source": note})
        if alert is None:
            continue
        alert["id"] = uuid.uuid4().hex
        alert["created_at"] = as_of
        _write(
            "INSERT INTO alerts (id, user_id, created_at, alert_type,"
            " ticker, title, body, severity, channel, rule_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (alert["id"], "local", alert["created_at"],
             alert["alert_type"], alert["ticker"], alert["title"],
             alert["body"], alert["severity"], "IN_APP", r["id"]))
        fired.append(_row_to_alert_sqlite(alert))

    return env({"fired": fired, "evaluated": per_rule_status},
               "UNCONFIRMED", request)


def _row_to_alert_sqlite(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": a["id"], "created_at": a["created_at"],
        "alert_type": a["alert_type"], "ticker": a.get("ticker"),
        "title": a["title"], "body": a.get("body", ""),
        "severity": a.get("severity", "INFO"), "channel": "IN_APP",
        "read_at": None,
    }


class DeliverRequest(BaseModel):
    channel: str = Field(pattern="^(push|email)$")
    to: Optional[str] = None


@router.post("/alerts/{alert_id}/deliver")
def deliver_alert(alert_id: str, body: DeliverRequest, request: Request):
    """Push/email delivery — real providers when configured.

    Sends via OneSignal (push) / Resend (email). Without credentials returns
    {"status": "not_configured", "reason": ...} naming the exact missing env
    var — nothing is ever faked. In-app delivery (GET /alerts) is the
    always-on channel.
    """
    row = _one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
    if row is None:
        not_found(f"alert {alert_id} not found")
    alert = _row_to_alert(row)
    if body.channel == "push":
        result = delivery.send_push(alert)
    else:
        result = delivery.send_email(alert, to=body.to)
    log.info("deliver %s via %s -> %s", alert_id, body.channel,
             result["status"])
    return env(result, "UNCONFIRMED", request)

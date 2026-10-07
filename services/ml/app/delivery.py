"""Out-of-app delivery channels (ARGUS Phase 7 production hardening).

In-app delivery is REAL: fired alerts are persisted and served by
``GET /api/v1/alerts``. Push goes through OneSignal and email through
Resend when their credentials are configured; otherwise the delivery is
explicitly reported as ``{"status": "not_configured", "reason": ...}``
— never a faked send, and the alert is ALWAYS kept in-app regardless.

Env vars (see README "Notifications setup"):
    ONESIGNAL_API_KEY, ONESIGNAL_APP_ID — push via OneSignal
    RESEND_API_KEY, RESEND_FROM         — email via Resend
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.notifications import NotConfiguredError
from app.notifications.email import send_email_resend
from app.notifications.push import send_push_onesignal

log = logging.getLogger("argus.ml.delivery")


def _alert_text(alert: Dict[str, Any]) -> tuple[str, str]:
    """(subject/title, body) rendered from the alert dict."""
    severity = alert.get("severity") or "INFO"
    title = alert.get("title") or f"ARGUS {severity} alert"
    ticker = alert.get("ticker")
    body = alert.get("body") or ""
    subject = f"[ARGUS {severity}] {title}"
    if ticker:
        subject = f"[{ticker}] {subject}"
    return subject, body


def send_push(alert: Dict[str, Any]) -> Dict[str, Any]:
    """Deliver an alert via push (OneSignal) when configured.

    Returns one of:
      {"status": "delivered", "provider": "onesignal", "id": ...}
      {"status": "error", "provider": "onesignal", "reason": ...}
      {"status": "not_configured", "reason": ...}
    The alert is always kept in-app regardless of the outcome.
    """
    alert_id = alert.get("id")
    try:
        title, body = _alert_text(alert)
        result = send_push_onesignal(
            title=title, body=body,
            alert_id=str(alert_id) if alert_id is not None else None,
            ticker=alert.get("ticker"))
        if result["status"] == "error":
            log.warning("PUSH delivery ERROR for alert '%s': %s",
                        alert_id, result["reason"])
        else:
            log.info("PUSH delivery delivered for alert '%s' (id=%s)",
                     alert_id, result.get("id"))
        return result
    except NotConfiguredError as exc:
        log.warning(
            "PUSH delivery not configured — alert '%s' (%s) NOT sent. "
            "Missing env var: %s", alert_id, alert.get("title"),
            exc.env_var)
        return {
            "status": "not_configured",
            "reason": ("Set ONESIGNAL_API_KEY and ONESIGNAL_APP_ID to enable "
                       "push delivery. Alert was kept in-app only; nothing "
                       "was sent."),
        }


def send_email(alert: Dict[str, Any],
               to: Optional[str] = None) -> Dict[str, Any]:
    """Deliver an alert via email (Resend) when configured.

    Requires a recipient address ``to`` and a configured RESEND_API_KEY.
    Returns one of:
      {"status": "delivered", "provider": "resend", "id": ...}
      {"status": "error", "provider": "resend", "reason": ...}
      {"status": "not_configured", "reason": ...}
    The alert is always kept in-app regardless of the outcome.
    """
    alert_id = alert.get("id")
    if not to:
        log.warning(
            "EMAIL delivery not_configured — alert '%s' NOT sent: no "
            "recipient address provided. Configure RESEND_API_KEY and pass "
            "'to' to enable.", alert_id)
        return {
            "status": "not_configured",
            "reason": ("Email recipient required: pass a 'to' address to the "
                       "deliver endpoint, and set RESEND_API_KEY (with a "
                       "verified sender domain via RESEND_FROM) to enable "
                       "email delivery. Alert was kept in-app only; nothing "
                       "was sent."),
        }
    try:
        subject, body = _alert_text(alert)
        result = send_email_resend(to=to, subject=subject, text=body)
        if result["status"] == "error":
            log.warning("EMAIL delivery ERROR for alert '%s' to %s: %s",
                        alert_id, to, result["reason"])
        else:
            log.info("EMAIL delivery delivered for alert '%s' (id=%s)",
                     alert_id, result.get("id"))
        return result
    except NotConfiguredError as exc:
        log.warning(
            "EMAIL delivery not configured — alert '%s' NOT sent to %s. "
            "Missing env var: %s", alert_id, to, exc.env_var)
        return {
            "status": "not_configured",
            "reason": ("RESEND_API_KEY is not set (also set RESEND_FROM to a "
                       "sender address on a verified Resend domain). Alert "
                       "was kept in-app only; nothing was sent."),
        }

"""Push delivery via the OneSignal REST API.

Endpoint: ``POST https://api.onesignal.com/notifications``
Auth: ``Authorization: Basic $ONESIGNAL_API_KEY``
Payload: ``{"app_id": $ONESIGNAL_APP_ID,
            "included_segments": ["Subscribed Users"],
            "headings": {"en": title}, "contents": {"en": body},
            "data": {"alert_id": ..., "ticker": ...}}``

Env vars:
    ONESIGNAL_API_KEY  (required) — REST API key from OneSignal dashboard
        (https://onesignal.com → Settings → Keys & IDs)
    ONESIGNAL_APP_ID   (required) — App ID from the same dashboard page.
"""

from __future__ import annotations

import logging
import os

import httpx

from . import NotConfiguredError

log = logging.getLogger("argus.ml.notifications.push")

ONESIGNAL_URL = "https://api.onesignal.com/notifications"


def send_push_onesignal(title: str, body: str, alert_id: str | None = None,
                        ticker: str | None = None) -> dict:
    """Send one push notification via OneSignal to all subscribed users.

    Raises:
        NotConfiguredError: if ``ONESIGNAL_API_KEY`` or ``ONESIGNAL_APP_ID``
            is not set (names the exact missing var).
    Returns:
        ``{"status": "delivered", "provider": "onesignal", "id": ...}`` on 200;
        ``{"status": "error", "provider": "onesignal", "reason": ...}`` on
        provider HTTP errors (never raises on provider errors).
    """
    api_key = os.environ.get("ONESIGNAL_API_KEY")
    if not api_key:
        raise NotConfiguredError("ONESIGNAL_API_KEY")
    app_id = os.environ.get("ONESIGNAL_APP_ID")
    if not app_id:
        raise NotConfiguredError("ONESIGNAL_APP_ID")

    log.info("OneSignal push send attempted for alert %s", alert_id)
    try:
        resp = httpx.post(
            ONESIGNAL_URL,
            headers={
                "Authorization": f"Basic {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "app_id": app_id,
                "included_segments": ["Subscribed Users"],
                "headings": {"en": title},
                "contents": {"en": body},
                "data": {"alert_id": alert_id, "ticker": ticker},
            },
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        log.warning("OneSignal push send failed: %s", exc)
        return {"status": "error", "provider": "onesignal",
                "reason": f"network error: {exc}"}

    if resp.status_code == 200:
        message_id = (resp.json() or {}).get("id")
        log.info("OneSignal push send delivered (id=%s)", message_id)
        return {"status": "delivered", "provider": "onesignal",
                "id": message_id}

    body_snippet = (resp.text or "")[:300]
    log.warning("OneSignal push send error: HTTP %s %s", resp.status_code,
                body_snippet)
    return {
        "status": "error",
        "provider": "onesignal",
        "reason": f"HTTP {resp.status_code}: {body_snippet}",
    }

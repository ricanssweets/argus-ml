"""Email delivery via the Resend HTTP API.

Endpoint: ``POST https://api.resend.com/emails``
Auth: ``Authorization: Bearer $RESEND_API_KEY``
Payload: ``{"from": ..., "to": [...], "subject": ..., "text": ...}``

Env vars:
    RESEND_API_KEY  (required) — API key from https://resend.com/api-keys
    RESEND_FROM     (optional) — sender address, default ``alerts@argus.local``.
        NOTE: Resend requires the sender to use a VERIFIED domain — get one
        at https://resend.com/domains and set RESEND_FROM to an address on
        that domain, e.g. ``alerts@yourdomain.com``. Unverified senders are
        rejected (403) and the send is reported as an error dict.
"""

from __future__ import annotations

import logging
import os

import httpx

from . import NotConfiguredError

log = logging.getLogger("argus.ml.notifications.email")

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "alerts@argus.local"


def send_email_resend(to: str, subject: str, text: str) -> dict:
    """Send one email via Resend.

    Raises:
        NotConfiguredError: if ``RESEND_API_KEY`` is not set.
    Returns:
        ``{"status": "delivered", "provider": "resend", "id": ...}`` on 200;
        ``{"status": "error", "provider": "resend", "reason": ...}`` on
        provider HTTP errors (never raises on provider errors).
    """
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise NotConfiguredError("RESEND_API_KEY")

    sender = os.environ.get("RESEND_FROM", DEFAULT_FROM)
    log.info("Resend email send attempted to=<1 recipient> subject=%r",
             subject)
    try:
        resp = httpx.post(
            RESEND_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"from": sender, "to": [to], "subject": subject,
                  "text": text},
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        # Network-level failure (DNS, timeout, connection): provider error,
        # never raised.
        log.warning("Resend email send failed: %s", exc)
        return {"status": "error", "provider": "resend",
                "reason": f"network error: {exc}"}

    if resp.status_code == 200:
        message_id = (resp.json() or {}).get("id")
        log.info("Resend email send delivered (id=%s)", message_id)
        return {"status": "delivered", "provider": "resend",
                "id": message_id}

    body_snippet = (resp.text or "")[:300]
    log.warning("Resend email send error: HTTP %s %s", resp.status_code,
                body_snippet)
    return {
        "status": "error",
        "provider": "resend",
        "reason": f"HTTP {resp.status_code}: {body_snippet}",
    }

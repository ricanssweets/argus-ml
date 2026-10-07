"""ARGUS out-of-app notification providers (Phase 7 production hardening).

- :class:`NotConfiguredError` — raised when a required provider credential
  is missing. Carries ``.env_var`` naming the EXACT env var that is absent.
- :mod:`app.notifications.email` — Resend email provider.
- :mod:`app.notifications.push` — OneSignal push provider.

Contract shared by both providers:

- ``NotConfiguredError(<exact env var name>)`` when credentials are missing
  (name the EXACT env var — never a paraphrase).
- ``{"status": "delivered", "provider": ..., "id": <provider id>}`` on 200.
- ``{"status": "error", "provider": ..., "reason": <http status + body>}``
  on provider HTTP errors — never raise on provider errors, never silent.
- Timeouts 10s. No secrets in logs (log only attempt + status).
"""


class NotConfiguredError(RuntimeError):
    """A provider credential env var is not set.

    Attributes:
        env_var: the EXACT name of the missing env var.
    """

    def __init__(self, env_var: str):
        super().__init__(
            f"Notification provider not configured: set {env_var}.")
        self.env_var = env_var

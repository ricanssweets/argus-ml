"""Verified-identity + ownership enforcement for the ARGUS ml service.

Why this module exists
----------------------
``app/middleware.py`` verified the Supabase JWT and wrote
``request.state.user`` / ``request.state.authenticated`` — but **no router
ever read them**, and every alert row was written with the hardcoded
``user_id = "local"`` (``routers/alerts.py:176,273``, ``db.py:182,187``).
Result: no ownership enforcement anywhere (IDOR) and no way for a router to
learn who the caller is.

This module is the single reusable source of truth for identity:

* :func:`decode_token` — verify an HS256 Supabase JWT with optional
  audience/issuer validation (``SUPABASE_JWT_AUD`` / ``SUPABASE_JWT_ISS``).
* :func:`token_status` — cached verification outcome for one request; it
  never raises, so the *outer* rate-limit middleware can consult it without
  duplicating auth and without turning a bad token into a 500.
* :func:`token_identity` — claims of a token that ACTUALLY verified
  (``None`` otherwise). The rate limiter uses this: the mere presence of an
  ``Authorization`` header must never escalate the bucket.
* :func:`effective_claims` — verified token claims, or the explicit
  ``AUTH_DISABLED`` dev-bypass identity when (and only when) the process was
  started with an explicit dev opt-in (``ARGUS_ENV=development`` or
  ``ARGUS_ALLOW_AUTH_DISABLED=1``; see ``middleware.assert_auth_configuration``).
* :func:`current_subject` / :func:`require_subject` — the verified subject,
  or HTTP 401.
* :func:`ensure_owner` / :func:`require_owner` — ownership check, HTTP 403
  (404 when the caller must not learn the resource exists).

Routers enforce ownership by depending on :func:`require_subject` and by
scoping every read/write with the returned subject; the exact per-router
patches live in ``changes/C-security-reliability.md``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from fastapi import HTTPException, Request

log = logging.getLogger("argus.ml.authz")

# Explicit local/dev opt-ins. AUTH_DISABLED is refused at import time
# (middleware.assert_auth_configuration) unless one of these is set.
_DEV_ENVS = {"development", "dev", "local", "test", "testing"}
_TRUTHY = {"1", "true", "yes", "on"}

# Identity granted by the AUTH_DISABLED dev bypass (never in production:
# assert_auth_configuration() refuses to import without the dev opt-in).
DEV_CLAIMS: Dict[str, Any] = {"sub": "dev", "email": "dev@localhost", "role": "dev"}

# Legacy hardcoded owner used before this module existed (db.py:182,187 and
# routers/alerts.py:176,273). Kept as a named constant for the data migration
# note in the report — it is NOT used as a fallback identity anywhere.
LEGACY_USER_ID = "local"

_JWT_ALGORITHMS = ("HS256",)
_TOKEN_STATE_KEY = "_argus_token_status"  # (claims, reason) cached per request


# ---------------------------------------------------------------------------
# Process configuration (dev opt-in / secrets)
# ---------------------------------------------------------------------------

def auth_disabled() -> bool:
    """True when ``AUTH_DISABLED`` is explicitly enabled."""
    return os.environ.get("AUTH_DISABLED", "").strip().lower() == "true"


def dev_optin_source() -> Optional[str]:
    """Name of the env var that switched this process into dev mode, if any."""
    if os.environ.get("ARGUS_ENV", "").strip().lower() in _DEV_ENVS:
        return "ARGUS_ENV"
    if os.environ.get("ARGUS_ALLOW_AUTH_DISABLED", "").strip().lower() in _TRUTHY:
        return "ARGUS_ALLOW_AUTH_DISABLED"
    return None


def dev_mode_explicit() -> bool:
    """True only when the dev mode was opted into explicitly (fail closed)."""
    return dev_optin_source() is not None


def jwt_secret() -> Optional[str]:
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if secret is None:
        return None
    secret = secret.strip()
    return secret or None


def jwt_audience() -> Optional[str]:
    aud = os.environ.get("SUPABASE_JWT_AUD")
    return aud.strip() if aud and aud.strip() else None


def jwt_issuer() -> Optional[str]:
    iss = os.environ.get("SUPABASE_JWT_ISS")
    return iss.strip() if iss and iss.strip() else None


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------

class AuthNotConfigured(Exception):
    """No ``SUPABASE_JWT_SECRET`` — the server cannot verify tokens."""


class InvalidToken(Exception):
    """Missing/expired/bad-signature token, or wrong audience/issuer."""


def decode_token(token: str) -> Dict[str, Any]:
    """Verify a Supabase JWT and return its claims.

    Audience/issuer checks are enabled whenever ``SUPABASE_JWT_AUD`` /
    ``SUPABASE_JWT_ISS`` are configured; when they are not, behaviour is
    unchanged (no ``verify_aud: False`` is forced — python-jose's own
    default applies, and the omission is logged once by the caller).

    Raises :class:`AuthNotConfigured` or :class:`InvalidToken`.
    """
    secret = jwt_secret()
    if not secret:
        raise AuthNotConfigured()
    aud = jwt_audience()
    iss = jwt_issuer()
    options = {
        "verify_signature": True,
        "verify_exp": True,
        "verify_aud": aud is not None,
        "verify_iss": iss is not None,
    }
    kwargs: Dict[str, Any] = {
        "algorithms": list(_JWT_ALGORITHMS),
        "options": options,
    }
    if aud is not None:
        kwargs["audience"] = aud
    if iss is not None:
        kwargs["issuer"] = iss
    try:
        from jose import jwt as jose_jwt
        claims = jose_jwt.decode(token, secret, **kwargs)
    except Exception as exc:  # noqa: BLE001 — every failure is "invalid token"
        raise InvalidToken(str(exc)) from exc
    if not isinstance(claims, dict):  # pragma: no cover — jose always returns a dict
        raise InvalidToken("token payload is not a JSON object")
    if aud is not None and claims.get("aud") is None:
        # python-jose accepts a missing aud claim even with verify_aud=True;
        # when an audience is configured it MUST be present.
        raise InvalidToken("token has no 'aud' claim but an audience is required")
    if iss is not None and claims.get("iss") is None:
        raise InvalidToken("token has no 'iss' claim but an issuer is required")
    return claims


def _scope_state(request: Request) -> Dict[str, Any]:
    """The request-scoped state dict shared by every middleware layer."""
    return request.scope.setdefault("state", {})


def token_status(request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """``(claims, reason)`` for this request, verified once and cached.

    ``claims`` is non-None only for a token that actually verified.
    ``reason`` is one of ``None``, ``"missing"``, ``"not_configured"``,
    ``"invalid"``. Never raises.
    """
    state = _scope_state(request)
    cached = state.get(_TOKEN_STATE_KEY)
    if cached is not None:
        return cached
    claims, reason = _verify_bearer(request)
    state[_TOKEN_STATE_KEY] = (claims, reason)
    return claims, reason


def _verify_bearer(request: Request) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    header = request.headers.get("Authorization", "")
    if not header.lower().startswith("bearer "):
        return None, "missing"
    token = header[7:].strip()
    if not token:
        return None, "missing"
    try:
        claims = decode_token(token)
    except AuthNotConfigured:
        return None, "not_configured"
    except InvalidToken:
        return None, "invalid"
    if not str(claims.get("sub") or "").strip():
        # A token with no subject cannot identify an owner: treat as invalid
        # instead of silently propagating sub=None into user-scoped queries.
        return None, "invalid"
    return claims, None


def token_identity(request: Request) -> Optional[Dict[str, Any]]:
    """Claims of a token that actually verified, else ``None``.

    Used by ``RateLimitMiddleware`` (which runs outside ``AuthMiddleware``):
    an ``Authorization`` header that does not verify must NOT get the
    elevated bucket.
    """
    claims, _reason = token_status(request)
    return claims


def effective_claims(request: Request) -> Optional[Dict[str, Any]]:
    """Verified claims, or the explicit AUTH_DISABLED dev identity."""
    claims, _reason = token_status(request)
    if claims:
        return claims
    if auth_disabled() and dev_mode_explicit():
        return dict(DEV_CLAIMS)
    return None


# ---------------------------------------------------------------------------
# Router-facing API (401 / 403)
# ---------------------------------------------------------------------------

def current_claims(request: Request) -> Dict[str, Any]:
    """Verified claims for this request or HTTP 401."""
    claims = effective_claims(request)
    if not claims:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return claims


def current_subject(request: Request) -> str:
    """Verified subject (``sub``) for this request or HTTP 401.

    This is the value routers must scope every user-owned read/write with
    (``user_id = current_subject(request)``). Never hardcode ``"local"``.
    """
    claims = current_claims(request)
    sub = str(claims.get("sub") or "").strip()
    if not sub:
        raise HTTPException(status_code=401, detail="Token has no subject claim.")
    return sub


def require_subject(request: Request) -> str:
    """FastAPI dependency form: ``user_id: str = Depends(authz.require_subject)``."""
    return current_subject(request)


def require_owner(request: Request, owner_user_id: Optional[str], *,
                  conceal: bool = False) -> str:
    """HTTP 403 unless ``owner_user_id`` is the verified subject.

    ``conceal=True`` returns 404 instead, so a caller cannot enumerate other
    users' resource ids.
    """
    subject = current_subject(request)
    if owner_user_id is not None and str(owner_user_id) == subject:
        return subject
    if conceal:
        raise HTTPException(status_code=404, detail="Resource not found.")
    raise HTTPException(
        status_code=403,
        detail="You do not have access to this resource.")


def ensure_owner(row: Optional[Dict[str, Any]], request: Request, *,
                 column: str = "user_id", conceal: bool = True) -> Dict[str, Any]:
    """Validate ownership of a fetched row; 404 when missing, 403/404 on mismatch.

    Returns the row on success so callers can write
    ``row = authz.ensure_owner(_one(...), request)``.
    """
    if row is None:
        raise HTTPException(status_code=404, detail="Resource not found.")
    owner = row.get(column)
    if owner is None or str(owner) != current_subject(request):
        if conceal:
            raise HTTPException(status_code=404, detail="Resource not found.")
        raise HTTPException(
            status_code=403,
            detail="You do not have access to this resource.")
    return row


def dependency_require_owner(column_value: str, *, conceal: bool = True):
    """Build a dependency-free ownership check for a known owner value."""
    def _check(request: Request) -> str:
        return require_owner(request, column_value, conceal=conceal)
    return _check


def scoped_user_id(request: Request) -> str:
    """Alias kept explicit for INSERT statements: the owner to persist."""
    return current_subject(request)


_aud_logged = False


def reset_for_tests() -> None:
    """Clear the one-time log flags (tests only)."""
    global _aud_logged
    _aud_logged = False


def warn_audience_unset() -> None:
    """Log once that audience validation is disabled because it is unset."""
    global _aud_logged
    if not _aud_logged:
        log.warning(
            "SUPABASE_JWT_AUD is not set — JWT audience validation is DISABLED. "
            "Set SUPABASE_JWT_AUD (and SUPABASE_JWT_ISS) to enable it.")
        _aud_logged = True

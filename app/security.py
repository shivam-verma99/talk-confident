"""JWT issuance/verification and Google ID-token verification.

The Android client obtains a Google ID token via the Credential Manager / Sign-In
APIs and posts it to ``/auth/google``. We verify the token using Google's public
keys and then issue our own short-lived JWT for subsequent calls.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import anyio
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from jose import JWTError, jwt

from app.config import get_settings


# --------------------------------------------------------------------------- errors


class AuthError(Exception):
    """Generic authentication failure."""


class GoogleIdTokenError(AuthError):
    """Google ID token failed verification."""


class InvalidJWTError(AuthError):
    """Our own JWT failed verification."""


# --------------------------------------------------------------------------- payloads


@dataclass(frozen=True)
class GoogleIdentity:
    """Minimal identity extracted from a verified Google ID token."""

    sub: str
    email: str
    email_verified: bool
    name: str | None
    picture: str | None


# --------------------------------------------------------------------------- JWT


def create_access_token(
    *,
    subject: uuid.UUID | str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Sign and return a JWT for ``subject``."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.jwt_expires_minutes)).timestamp()),
        "iss": settings.app_name,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate one of our own JWTs."""
    settings = get_settings()
    try:
        return jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub"]},
        )
    except JWTError as exc:  # pragma: no cover - tested via integration
        raise InvalidJWTError(str(exc)) from exc


# --------------------------------------------------------------------------- Google


def _verify_google_token_sync(token: str, audiences: list[str]) -> dict[str, Any]:
    """Blocking verification — wrapped by :func:`verify_google_id_token`."""
    if not audiences:
        raise GoogleIdTokenError(
            "GOOGLE_OAUTH_CLIENT_ID is not configured on the server."
        )
    last_error: Exception | None = None
    for audience in audiences:
        try:
            return google_id_token.verify_oauth2_token(
                token,
                google_requests.Request(),
                audience=audience,
            )
        except ValueError as exc:
            last_error = exc
            continue
    raise GoogleIdTokenError(
        f"Google ID token verification failed for all configured audiences: {last_error}"
    )


async def verify_google_id_token(token: str) -> GoogleIdentity:
    """Verify a Google ID token and return a typed identity.

    Network/crypto work runs in a worker thread so we don't block the event loop.
    """
    settings = get_settings()
    claims = await anyio.to_thread.run_sync(
        _verify_google_token_sync, token, settings.google_audiences
    )

    sub = claims.get("sub")
    email = claims.get("email")
    if not sub or not email:
        raise GoogleIdTokenError("Google ID token is missing required claims.")
    if claims.get("email_verified") is False:
        raise GoogleIdTokenError("Google reports the email as unverified.")

    return GoogleIdentity(
        sub=str(sub),
        email=str(email),
        email_verified=bool(claims.get("email_verified", True)),
        name=claims.get("name"),
        picture=claims.get("picture"),
    )

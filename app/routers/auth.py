"""Auth router — Google Sign-In exchange and ``/auth/me``."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Profile, User
from app.deps import CurrentUser, DBSession
from app.schemas.auth import (
    GoogleLoginRequest,
    TokenResponse,
    UserOut,
    UserUpdateRequest,
)
from app.security import (
    GoogleIdTokenError,
    create_access_token,
    verify_google_id_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/google", response_model=TokenResponse)
async def google_login(payload: GoogleLoginRequest, db: DBSession) -> TokenResponse:
    """Exchange a Google ID token for our own JWT.

    First-time logins create a ``User`` + ``Profile`` row automatically. No password
    is ever stored.
    """
    try:
        identity = await verify_google_id_token(payload.id_token)
    except GoogleIdTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    # Find existing user by google_sub or by email (account-linking on email).
    user = (
        await db.execute(select(User).where(User.google_sub == identity.sub))
    ).scalar_one_or_none()
    if user is None:
        user = (
            await db.execute(select(User).where(User.email == identity.email))
        ).scalar_one_or_none()
        if user is not None and not user.google_sub:
            user.google_sub = identity.sub

    if user is None:
        user = User(
            google_sub=identity.sub,
            email=identity.email,
            full_name=identity.name,
            picture_url=identity.picture,
        )
        db.add(user)
        await db.flush()
        db.add(Profile(user_id=user.id))
    else:
        if identity.name and not user.full_name:
            user.full_name = identity.name
        if identity.picture and not user.picture_url:
            user.picture_url = identity.picture

    user.last_login_at = datetime.now(timezone.utc)
    await db.flush()
    await db.commit()

    settings = get_settings()
    token = create_access_token(subject=user.id)
    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_expires_minutes * 60,
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
async def me(current_user: CurrentUser) -> UserOut:
    return UserOut.model_validate(current_user)


@router.patch("/me", response_model=UserOut)
async def update_me(
    payload: UserUpdateRequest,
    current_user: CurrentUser,
    db: DBSession,
) -> UserOut:
    """Update mutable user fields. Currently: pronouns.

    Uses ``model_fields_set`` so we can distinguish "field not sent" from
    "field explicitly set to null/empty" — the latter clears the value.
    """
    if "pronouns" in payload.model_fields_set:
        # Normalise empty string to null so we never store a meaningless ""
        cleaned = (payload.pronouns or "").strip() or None
        current_user.pronouns = cleaned
    await db.commit()
    await db.refresh(current_user)
    return UserOut.model_validate(current_user)

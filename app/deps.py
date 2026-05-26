"""Shared FastAPI dependencies — DB session, current user, Gemini client."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from google import genai
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db.base import get_session_factory
from app.db.models import User
from app.security import InvalidJWTError, decode_access_token
from sqlalchemy import select


# Tokens are sent as ``Authorization: Bearer <jwt>``.  We point tokenUrl at the
# Google exchange endpoint for nicer OpenAPI ergonomics.
_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/google", auto_error=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """Yield a per-request async DB session."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


DBSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_genai_client(request: Request) -> genai.Client:
    """Return the process-wide Gemini client created during lifespan startup."""
    client = getattr(request.app.state, "genai_client", None)
    if client is None:  # pragma: no cover - configuration error
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gemini client is not initialised.",
        )
    return client


GenAIClient = Annotated[genai.Client, Depends(get_genai_client)]


async def get_current_user(
    token: Annotated[str | None, Depends(_oauth2_scheme)],
    db: DBSession,
) -> User:
    """Resolve the current user from a bearer JWT."""
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_access_token(token)
    except InvalidJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token subject is not a valid user id.",
        ) from exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User no longer exists.",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

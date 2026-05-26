"""Gemini Explicit Context Caching wrapper.

A user's persona system instruction + compressed history is cacheable for ~1 hour and
dramatically reduces token cost on repeat evaluations. We lazily create the cache,
persist its resource name on the user's Profile, and rotate it when the summary changes.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from google import genai
from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import Profile, User
from app.prompts.persona import build_persona_instruction

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CachedContextHandle:
    """Either a Gemini cache name to attach, or fall-back system instruction text."""

    cache_name: str | None
    fallback_system_instruction: str | None

    @property
    def is_cached(self) -> bool:
        return self.cache_name is not None


async def get_or_create_user_cache(
    *,
    client: genai.Client,
    db: AsyncSession,
    user: User,
    profile: Profile,
) -> CachedContextHandle:
    """Return a usable handle to the user's persona context for Gemini calls.

    Falls back gracefully to a non-cached system instruction if cache creation fails
    (e.g. transient API error) so requests still succeed.
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)

    if (
        profile.active_cache_name
        and profile.cache_expires_at
        and profile.cache_expires_at > now + timedelta(minutes=2)
    ):
        return CachedContextHandle(cache_name=profile.active_cache_name, fallback_system_instruction=None)

    system_instruction = build_persona_instruction(user, profile.compressed_history)

    try:
        cache = await asyncio.to_thread(
            client.caches.create,
            model=settings.gemini_model,
            config=types.CreateCachedContentConfig(
                system_instruction=system_instruction,
                ttl=f"{settings.gemini_cache_ttl_seconds}s",
                display_name=f"talk-confident:{user.id}",
            ),
        )
    except Exception as exc:
        logger.warning("Cache create failed for user %s, falling back: %s", user.id, exc)
        return CachedContextHandle(cache_name=None, fallback_system_instruction=system_instruction)

    profile.active_cache_name = cache.name
    profile.cache_expires_at = now + timedelta(seconds=settings.gemini_cache_ttl_seconds)
    await db.flush()
    return CachedContextHandle(cache_name=cache.name, fallback_system_instruction=None)


async def invalidate_user_cache(
    *,
    client: genai.Client,
    db: AsyncSession,
    profile: Profile,
) -> None:
    """Drop the user's cached content (called after a new summary is generated)."""
    name = profile.active_cache_name
    profile.active_cache_name = None
    profile.cache_expires_at = None
    await db.flush()
    if not name:
        return
    try:
        await asyncio.to_thread(client.caches.delete, name=name)
    except Exception as exc:  # pragma: no cover - cleanup is best-effort
        logger.warning("Cache delete failed for %s: %s", name, exc)

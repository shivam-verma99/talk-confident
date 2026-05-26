"""Gemini orchestration — curriculum, audio evaluation, summarization.

All public functions are ``async`` and idempotent at the API level. Every Gemini
call is wrapped in ``tenacity`` retries for 429 / 5xx / transient ``APIError``s.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import get_settings
from app.db.models import ContextSummary, PracticeAttempt, Profile, User
from app.prompts.curriculum import (
    CURRICULUM_INSTRUCTION,
    CURRICULUM_RESPONSE_SCHEMA,
    MEETING_SCENARIO_INSTRUCTION,
    MEETING_SCENARIO_RESPONSE_SCHEMA,
)
from app.prompts.evaluation import (
    EVALUATION_INSTRUCTION,
    EVALUATION_RESPONSE_SCHEMA,
    LEVEL_RECOMMENDATION_INSTRUCTION,
    LEVEL_RECOMMENDATION_RESPONSE_SCHEMA,
    MEETING_TURN_INSTRUCTION,
    MEETING_TURN_RESPONSE_SCHEMA,
)
from app.prompts.persona import build_persona_instruction
from app.prompts.summarization import (
    SUMMARIZATION_INSTRUCTION,
    SUMMARIZATION_RESPONSE_SCHEMA,
)
from app.schemas.curriculum import CurriculumResponse
from app.schemas.practice import EvaluationResult, MeetingScenario
from app.schemas.progress import LevelRecommendation
from app.services.cache_service import (
    CachedContextHandle,
    get_or_create_user_cache,
    invalidate_user_cache,
)
from app.services.progress_service import gather_recent_weakness_signals

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- errors


class AIServiceError(RuntimeError):
    """Generic Gemini-side failure surfaced to callers."""


class AIRateLimitError(AIServiceError):
    """Gemini returned a quota / rate-limit error after retries."""


# --------------------------------------------------------------------------- internals


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, genai_errors.APIError):
        status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
        if status is None:
            # On unknown shapes, assume transient.
            return True
        try:
            status_int = int(status)
        except (TypeError, ValueError):
            return True
        return status_int == 429 or status_int >= 500
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError)):
        return True
    return False


def _retryer():
    return AsyncRetrying(
        retry=retry_if_exception_type((genai_errors.APIError, asyncio.TimeoutError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )


async def _generate_json(
    *,
    client: genai.Client,
    contents: list[Any],
    response_schema: dict[str, Any],
    cache_handle: CachedContextHandle | None,
    extra_instruction: str | None = None,
) -> tuple[dict[str, Any], int]:
    """Call ``generate_content`` and return ``(parsed_json, tokens_used)``.

    Combines persona caching with a per-call task instruction. If a cache is
    attached, ``system_instruction`` is omitted (the cache already contains it).
    """
    settings = get_settings()
    config_kwargs: dict[str, Any] = {
        "response_mime_type": "application/json",
        "response_schema": response_schema,
    }

    if cache_handle and cache_handle.is_cached:
        config_kwargs["cached_content"] = cache_handle.cache_name
    elif cache_handle and cache_handle.fallback_system_instruction:
        # Fallback: pass the persona inline this once.
        config_kwargs["system_instruction"] = cache_handle.fallback_system_instruction

    if extra_instruction:
        # Per-call instruction goes at the head of contents so the cached persona stays stable.
        contents = [types.Part.from_text(text=extra_instruction), *contents]

    config = types.GenerateContentConfig(**config_kwargs)

    last_exc: BaseException | None = None
    async for attempt in _retryer():
        with attempt:
            try:
                response = await asyncio.to_thread(
                    client.models.generate_content,
                    model=settings.gemini_model,
                    contents=contents,
                    config=config,
                )
            except genai_errors.APIError as exc:  # pragma: no cover - exercised via tests
                last_exc = exc
                if not _is_retryable(exc):
                    raise AIServiceError(str(exc)) from exc
                raise
    if response is None:  # pragma: no cover - defensive
        raise AIServiceError(f"Gemini returned no response: {last_exc}")

    text = (response.text or "").strip()
    if not text:
        raise AIServiceError("Gemini returned an empty response body.")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIServiceError(f"Gemini response was not valid JSON: {exc}") from exc

    usage = getattr(response, "usage_metadata", None)
    tokens_used = int(getattr(usage, "total_token_count", 0)) if usage else 0
    return payload, tokens_used


# --------------------------------------------------------------------------- curriculum


@dataclass
class _CurriculumContext:
    weaknesses: list[str]
    strengths: list[str]


async def _build_curriculum_context(
    db: AsyncSession, user_id: uuid.UUID
) -> _CurriculumContext:
    weaknesses, strengths = await gather_recent_weakness_signals(db=db, user_id=user_id)
    return _CurriculumContext(weaknesses=weaknesses, strengths=strengths)


async def generate_daily_curriculum(
    *,
    client: genai.Client,
    db: AsyncSession,
    user: User,
    profile: Profile,
    mode: str = "read_aloud",
) -> CurriculumResponse:
    """Generate today's vocabulary + practice sentences for ``user``."""
    ctx = await _build_curriculum_context(db, user.id)
    cache_handle = await get_or_create_user_cache(
        client=client, db=db, user=user, profile=profile
    )

    user_block = json.dumps(
        {
            "current_level": profile.current_level,
            "rolling_weaknesses": ctx.weaknesses,
            "recurring_strengths": ctx.strengths,
            "requested_mode": mode,
        },
        ensure_ascii=False,
    )

    payload, tokens = await _generate_json(
        client=client,
        contents=[types.Part.from_text(text=f"User context:\n{user_block}")],
        response_schema=CURRICULUM_RESPONSE_SCHEMA,
        cache_handle=cache_handle,
        extra_instruction=CURRICULUM_INSTRUCTION,
    )
    logger.info("curriculum generated for %s (tokens=%s)", user.id, tokens)
    return CurriculumResponse.model_validate(payload)


async def generate_meeting_scenario(
    *,
    client: genai.Client,
    db: AsyncSession,
    user: User,
    profile: Profile,
    focus_area: str | None,
) -> MeetingScenario:
    """Generate a single BSNL meeting scenario for spontaneous-speech practice."""
    cache_handle = await get_or_create_user_cache(
        client=client, db=db, user=user, profile=profile
    )
    ctx = await _build_curriculum_context(db, user.id)

    payload_block = json.dumps(
        {
            "focus_area": focus_area or "auto",
            "current_level": profile.current_level,
            "rolling_weaknesses": ctx.weaknesses,
        },
        ensure_ascii=False,
    )

    payload, tokens = await _generate_json(
        client=client,
        contents=[types.Part.from_text(text=f"Scenario context:\n{payload_block}")],
        response_schema=MEETING_SCENARIO_RESPONSE_SCHEMA,
        cache_handle=cache_handle,
        extra_instruction=MEETING_SCENARIO_INSTRUCTION,
    )
    logger.info("scenario generated for %s (tokens=%s)", user.id, tokens)
    return MeetingScenario.model_validate(payload)


# --------------------------------------------------------------------------- audio evaluation


@dataclass
class EvaluationOutcome:
    result: EvaluationResult
    raw_response: dict[str, Any]
    tokens_used: int


async def evaluate_audio_attempt(
    *,
    client: genai.Client,
    db: AsyncSession,
    user: User,
    profile: Profile,
    audio_part: types.Part,
    target_text: str | None,
    mode: str,
) -> EvaluationOutcome:
    """Evaluate one read-aloud / spontaneous attempt."""
    cache_handle = await get_or_create_user_cache(
        client=client, db=db, user=user, profile=profile
    )
    target_block = json.dumps(
        {
            "mode": mode,
            "target_text": target_text or "",
            "current_level": profile.current_level,
        },
        ensure_ascii=False,
    )

    payload, tokens = await _generate_json(
        client=client,
        contents=[
            types.Part.from_text(text=f"Attempt metadata:\n{target_block}"),
            audio_part,
        ],
        response_schema=EVALUATION_RESPONSE_SCHEMA,
        cache_handle=cache_handle,
        extra_instruction=EVALUATION_INSTRUCTION,
    )
    return EvaluationOutcome(
        result=EvaluationResult.model_validate(payload),
        raw_response=payload,
        tokens_used=tokens,
    )


@dataclass
class MeetingTurnOutcome:
    result: EvaluationResult
    next_prompt: str
    raw_response: dict[str, Any]
    tokens_used: int


async def evaluate_meeting_turn(
    *,
    client: genai.Client,
    db: AsyncSession,
    user: User,
    profile: Profile,
    audio_part: types.Part,
    scenario: MeetingScenario,
) -> MeetingTurnOutcome:
    """Evaluate one turn inside a Meeting Prep session AND advance the scenario."""
    cache_handle = await get_or_create_user_cache(
        client=client, db=db, user=user, profile=profile
    )
    scenario_block = scenario.model_dump_json(indent=2)
    instruction = MEETING_TURN_INSTRUCTION.format(scenario_block=scenario_block)

    payload, tokens = await _generate_json(
        client=client,
        contents=[audio_part],
        response_schema=MEETING_TURN_RESPONSE_SCHEMA,
        cache_handle=cache_handle,
        extra_instruction=instruction,
    )
    next_prompt = str(payload.pop("next_prompt", ""))
    return MeetingTurnOutcome(
        result=EvaluationResult.model_validate(payload),
        next_prompt=next_prompt,
        raw_response={**payload, "next_prompt": next_prompt},
        tokens_used=tokens,
    )


# --------------------------------------------------------------------------- level-up recommendation


async def recommend_level_up(
    *,
    client: genai.Client,
    db: AsyncSession,
    user: User,
    profile: Profile,
    rolling_stats: dict[str, Any],
) -> LevelRecommendation:
    """Ask Gemini whether the user is ready to advance."""
    cache_handle = await get_or_create_user_cache(
        client=client, db=db, user=user, profile=profile
    )
    block = json.dumps(rolling_stats, ensure_ascii=False, default=str)

    payload, tokens = await _generate_json(
        client=client,
        contents=[types.Part.from_text(text=f"Stats:\n{block}")],
        response_schema=LEVEL_RECOMMENDATION_RESPONSE_SCHEMA,
        cache_handle=cache_handle,
        extra_instruction=LEVEL_RECOMMENDATION_INSTRUCTION,
    )
    logger.info("level-up recommendation generated (tokens=%s)", tokens)
    return LevelRecommendation.model_validate(payload)


# --------------------------------------------------------------------------- summarization


async def summarize_history(
    *,
    client: genai.Client,
    db: AsyncSession,
    user_id: uuid.UUID,
) -> ContextSummary:
    """Compress the user's history into a fresh active summary.

    Rotates the Gemini cache so the next evaluation rebuilds it against the new summary.
    """
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one()

    # Gather recent attempt material.
    attempts_stmt = (
        select(PracticeAttempt)
        .where(PracticeAttempt.user_id == user_id)
        .order_by(PracticeAttempt.created_at.desc())
        .limit(40)
    )
    attempts = (await db.execute(attempts_stmt)).scalars().all()
    attempts_payload = [
        {
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "target_text": a.target_text,
            "transcript": a.transcript,
            "scores": {
                "pronunciation_clarity": a.pronunciation_score,
                "fluency": a.fluency_score,
                "confidence": a.confidence_score,
                "grammar": a.grammar_score,
            },
            "weaknesses": a.weaknesses_detected,
            "strengths": a.strengths_detected,
            "corrections": a.corrections,
        }
        for a in attempts
    ]

    # Build a one-shot system instruction here — we intentionally bypass the cache so
    # the summarization sees the *latest* prior summary explicitly.
    inline_persona = build_persona_instruction(user, profile.compressed_history)
    cache_handle = CachedContextHandle(
        cache_name=None, fallback_system_instruction=inline_persona
    )

    block = json.dumps(
        {
            "previous_summary": profile.compressed_history or "",
            "recent_attempts": attempts_payload,
        },
        ensure_ascii=False,
        default=str,
    )
    payload, tokens = await _generate_json(
        client=client,
        contents=[types.Part.from_text(text=f"Summarization inputs:\n{block}")],
        response_schema=SUMMARIZATION_RESPONSE_SCHEMA,
        cache_handle=cache_handle,
        extra_instruction=SUMMARIZATION_INSTRUCTION,
    )

    summary_text = str(payload.get("prose_summary", "")).strip()

    # Retire any prior active summary.
    prior_stmt = select(ContextSummary).where(
        ContextSummary.user_id == user_id, ContextSummary.is_active.is_(True)
    )
    for prior in (await db.execute(prior_stmt)).scalars().all():
        prior.is_active = False

    period_start = attempts[-1].created_at if attempts else None
    period_end = attempts[0].created_at if attempts else None
    summary = ContextSummary(
        user_id=user_id,
        summary_text=summary_text,
        summary_json=payload,
        period_start=period_start,
        period_end=period_end,
        token_count=tokens,
        is_active=True,
    )
    db.add(summary)

    profile.compressed_history = summary_text
    profile.compressed_token_count = tokens
    profile.last_summarized_at = datetime.now(timezone.utc)
    new_level = int(payload.get("current_level", profile.current_level))
    if 1 <= new_level <= 10:
        profile.current_level = new_level

    await invalidate_user_cache(client=client, db=db, profile=profile)
    await db.flush()
    logger.info("summary generated for %s (tokens=%s)", user_id, tokens)
    return summary

"""Wordlist orchestration — Gemini-backed analysis, sentence generation, definitions.

This module is the brain of the curated word-list feature.

* :func:`analyze_attempt_for_word_recommendations` runs after each ``/practice/evaluate``
  succeeds. It feeds the fresh attempt + recent weakness history into Gemini and
  returns 0-3 suggested ``coach`` words that target *patterns* (phoneme families,
  lexical gaps, grammar structures) rather than isolated mistakes.

* :func:`generate_sentences_for_words` produces a batch of practice sentences
  blended across the user's selected focus words (~70%) and recently mastered
  reinforcement words (~30%), calibrated to the user's current level.

* :func:`define_user_word` asks Gemini to fill in a definition + example when
  the user adds a bare word to their own list.

* :func:`apply_attempt_to_existing_words` updates ``mastery_score``,
  ``attempts_count``, ``last_practiced_at`` for any active words that appeared
  in the just-finished attempt's ``target_text``. Words whose mastery crosses
  the threshold are auto-archived with reason ``mastered``.

The Gemini calls re-use ``_generate_json`` from ``ai_service`` so they pick up
retries, response-schema enforcement, and persona caching for free.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from google import genai
from google.genai import types
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ArchiveReason,
    PracticeAttempt,
    Profile,
    User,
    WordListEntry,
    WordSource,
    WordStatus,
)
from app.prompts.wordlist import (
    SENTENCE_GENERATION_INSTRUCTION,
    SENTENCE_GENERATION_RESPONSE_SCHEMA,
    WORD_DEFINITION_INSTRUCTION,
    WORD_DEFINITION_RESPONSE_SCHEMA,
    WORDLIST_ANALYSIS_INSTRUCTION,
    WORDLIST_ANALYSIS_RESPONSE_SCHEMA,
)
from app.services.ai_service import _generate_json  # noqa: PLC2701 — intentional shared helper
from app.services.cache_service import get_or_create_user_cache
from app.services.progress_service import gather_recent_weakness_signals

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------- tunables

# Mastery threshold for auto-archiving. Empirically picked: with EMA alpha 0.35
# and 5+ attempts the score plateaus quickly when performance is consistent.
MASTERY_THRESHOLD = 85.0
MIN_ATTEMPTS_FOR_MASTERY = 5

# EMA smoothing for mastery_score updates.
MASTERY_EMA_ALPHA = 0.35

# Default mix ratio for sentence generation (focus vs reinforcement).
DEFAULT_FOCUS_RATIO = 0.7

# Max words the caller can supply as focus in one go.
MAX_FOCUS_WORDS = 6


# ---------------------------------------------------------------------------- helpers


def _tokenise(text: str) -> set[str]:
    """Lowercase + strip punctuation, return token set for word-presence checks."""
    return {t for t in re.findall(r"[a-zA-Z']+", text.lower()) if t}


def _word_in_text(word: str, target_text: str | None) -> bool:
    if not target_text:
        return False
    return word.lower() in _tokenise(target_text)


def _score_from_attempt(attempt: PracticeAttempt) -> float:
    """A single 0-100 figure that represents *speakability* for this attempt.

    Pronunciation + fluency are the only signals that meaningfully reflect how
    well the user said the words. Confidence and grammar are excluded because
    they may be high even when individual word articulation is poor.
    """
    return (attempt.pronunciation_score + attempt.fluency_score) / 2.0


# ---------------------------------------------------------------------------- update existing


async def apply_attempt_to_existing_words(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    attempt: PracticeAttempt,
) -> list[WordListEntry]:
    """Bump mastery / attempts_count / last_practiced_at for words in the attempt.

    Returns the entries that were touched. Auto-archives any entry whose mastery
    crosses ``MASTERY_THRESHOLD`` after this update.
    """
    if not attempt.target_text:
        return []

    target_tokens = _tokenise(attempt.target_text)
    if not target_tokens:
        return []

    stmt = select(WordListEntry).where(
        WordListEntry.user_id == user_id,
        WordListEntry.status == WordStatus.active,
    )
    entries = (await db.execute(stmt)).scalars().all()

    touched: list[WordListEntry] = []
    score = _score_from_attempt(attempt)
    now = datetime.now(timezone.utc)
    for entry in entries:
        if entry.word.lower() not in target_tokens:
            continue
        entry.attempts_count += 1
        # EMA smoothing — first sample seeds the score directly.
        if entry.attempts_count == 1 or entry.mastery_score == 0:
            entry.mastery_score = score
        else:
            entry.mastery_score = (
                MASTERY_EMA_ALPHA * score
                + (1 - MASTERY_EMA_ALPHA) * entry.mastery_score
            )
        entry.last_practiced_at = now
        if (
            entry.mastery_score >= MASTERY_THRESHOLD
            and entry.attempts_count >= MIN_ATTEMPTS_FOR_MASTERY
        ):
            entry.status = WordStatus.archived
            entry.archive_reason = ArchiveReason.mastered
            entry.archived_at = now
            logger.info(
                "auto-archived word '%s' for user %s (mastery=%.1f, attempts=%d)",
                entry.word,
                user_id,
                entry.mastery_score,
                entry.attempts_count,
            )
        touched.append(entry)
    return touched


# ---------------------------------------------------------------------------- analyse + suggest


@dataclass
class WordRecommendation:
    word: str
    definition: str
    example: str
    target_weakness: str
    why_chosen: str
    priority: int


async def _existing_words(db: AsyncSession, user_id: uuid.UUID) -> tuple[list[str], list[str]]:
    stmt = select(WordListEntry.word, WordListEntry.status).where(
        WordListEntry.user_id == user_id
    )
    rows = (await db.execute(stmt)).all()
    active: list[str] = []
    archived: list[str] = []
    for word, status in rows:
        if status == WordStatus.active:
            active.append(word)
        else:
            archived.append(word)
    return active, archived


async def analyze_attempt_for_word_recommendations(
    *,
    client: genai.Client,
    db: AsyncSession,
    user: User,
    profile: Profile,
    attempt: PracticeAttempt,
) -> list[WordListEntry]:
    """Run Gemini wordlist analysis against ``attempt`` and persist any new coach words.

    Returns the freshly created entries. Empty result is normal — many attempts
    do not reveal a new pattern worth adding to the list.
    """
    weaknesses, _strengths = await gather_recent_weakness_signals(
        db=db, user_id=user.id
    )
    active_words, archived_words = await _existing_words(db, user.id)

    cache_handle = await get_or_create_user_cache(
        client=client, db=db, user=user, profile=profile
    )

    block = json.dumps(
        {
            "attempt": {
                "target_text": attempt.target_text,
                "transcript": attempt.transcript,
                "scores": {
                    "pronunciation": attempt.pronunciation_score,
                    "fluency": attempt.fluency_score,
                    "confidence": attempt.confidence_score,
                    "grammar": attempt.grammar_score,
                },
                "weaknesses": attempt.weaknesses_detected or [],
                "corrections": attempt.corrections or [],
                "hesitation_markers": attempt.hesitation_markers or [],
                "overall_note": attempt.evaluator_feedback or "",
            },
            "recent_weaknesses": weaknesses,
            "active_words": active_words,
            "archived_words": archived_words,
            "current_level": profile.current_level,
            "user_pronouns": user.pronouns,
        },
        ensure_ascii=False,
    )

    try:
        payload, tokens = await _generate_json(
            client=client,
            contents=[types.Part.from_text(text=f"Analysis input:\n{block}")],
            response_schema=WORDLIST_ANALYSIS_RESPONSE_SCHEMA,
            cache_handle=cache_handle,
            extra_instruction=WORDLIST_ANALYSIS_INSTRUCTION,
        )
    except Exception as exc:  # pragma: no cover - background failures are logged
        logger.warning("wordlist analysis failed for user %s: %s", user.id, exc)
        return []

    logger.info(
        "wordlist analysis for %s -> %d recommendations (tokens=%s)",
        user.id,
        len(payload.get("recommended_words", [])),
        tokens,
    )

    created: list[WordListEntry] = []
    # Belt-and-braces: filter out anything Gemini suggested that already exists.
    blocked = {w.lower() for w in active_words + archived_words}
    for raw in payload.get("recommended_words", []) or []:
        word = str(raw.get("word", "")).strip()
        if not word or word.lower() in blocked:
            continue
        entry = WordListEntry(
            user_id=user.id,
            word=word,
            definition=str(raw.get("definition", "")).strip(),
            example=str(raw.get("example", "")).strip() or None,
            why_chosen=str(raw.get("why_chosen", "")).strip() or None,
            target_weakness=str(raw.get("target_weakness", "")).strip() or None,
            source=WordSource.coach,
            status=WordStatus.active,
            priority=float(raw.get("priority", 50)),
        )
        db.add(entry)
        created.append(entry)
        blocked.add(word.lower())
    if created:
        await db.flush()
    return created


# ---------------------------------------------------------------------------- generate sentences


def _pick_focus(
    entries: list[WordListEntry], explicit_ids: list[uuid.UUID] | None, count: int
) -> list[WordListEntry]:
    """Pick focus words. Explicit selection wins; otherwise rank by need."""
    if explicit_ids:
        ids = set(explicit_ids)
        return [e for e in entries if e.id in ids][:MAX_FOCUS_WORDS]
    # Need score = priority * (1 - mastery/100). Higher = needs more work.
    ranked = sorted(
        entries,
        key=lambda e: e.priority * max(0.05, 1.0 - e.mastery_score / 100.0),
        reverse=True,
    )
    return ranked[: min(MAX_FOCUS_WORDS, max(3, count // 2))]


def _pick_reinforcement(entries: list[WordListEntry], count: int) -> list[WordListEntry]:
    """Pick recently mastered words to weave in for retention practice."""
    mastered = [e for e in entries if e.archive_reason == ArchiveReason.mastered]
    mastered.sort(key=lambda e: e.archived_at or e.created_at, reverse=True)
    return mastered[: max(1, count // 3)]


async def generate_sentences_for_words(
    *,
    client: genai.Client,
    db: AsyncSession,
    user: User,
    profile: Profile,
    explicit_word_ids: list[uuid.UUID] | None,
    count: int,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Live-generate practice sentences. Returns (sentences, focus_words, reinforcement_words)."""
    stmt = select(WordListEntry).where(WordListEntry.user_id == user.id)
    all_entries = (await db.execute(stmt)).scalars().all()

    active = [e for e in all_entries if e.status == WordStatus.active]
    archived = [e for e in all_entries if e.status == WordStatus.archived]

    if not active:
        return [], [], []

    focus_entries = _pick_focus(active, explicit_word_ids, count)
    reinforce_entries = _pick_reinforcement(archived, count)

    cache_handle = await get_or_create_user_cache(
        client=client, db=db, user=user, profile=profile
    )

    block = json.dumps(
        {
            "current_level": profile.current_level,
            "focus_words": [
                {
                    "word": e.word,
                    "definition": e.definition,
                    "mastery_score": round(e.mastery_score, 1),
                    "target_weakness": e.target_weakness,
                }
                for e in focus_entries
            ],
            "reinforcement_words": [
                {"word": e.word, "definition": e.definition}
                for e in reinforce_entries
            ],
            "count": count,
            "focus_ratio": DEFAULT_FOCUS_RATIO,
            "user_pronouns": user.pronouns,
            "user_role": user.role,
        },
        ensure_ascii=False,
    )

    payload, tokens = await _generate_json(
        client=client,
        contents=[types.Part.from_text(text=f"Sentence-generation input:\n{block}")],
        response_schema=SENTENCE_GENERATION_RESPONSE_SCHEMA,
        cache_handle=cache_handle,
        extra_instruction=SENTENCE_GENERATION_INSTRUCTION,
    )
    logger.info(
        "sentence generation for %s -> %d sentences (tokens=%s)",
        user.id,
        len(payload.get("sentences", [])),
        tokens,
    )

    return (
        payload.get("sentences", []),
        [e.word for e in focus_entries],
        [e.word for e in reinforce_entries],
    )


# ---------------------------------------------------------------------------- define a bare word


@dataclass
class WordDefinition:
    valid: bool
    definition: str
    example: str


async def define_user_word(
    *,
    client: genai.Client,
    db: AsyncSession,
    user: User,
    profile: Profile,
    word: str,
) -> WordDefinition:
    """Ask Gemini for a definition + example for ``word``."""
    cache_handle = await get_or_create_user_cache(
        client=client, db=db, user=user, profile=profile
    )
    block = json.dumps({"word": word}, ensure_ascii=False)
    payload, _tokens = await _generate_json(
        client=client,
        contents=[types.Part.from_text(text=f"Word to define:\n{block}")],
        response_schema=WORD_DEFINITION_RESPONSE_SCHEMA,
        cache_handle=cache_handle,
        extra_instruction=WORD_DEFINITION_INSTRUCTION,
    )
    return WordDefinition(
        valid=bool(payload.get("valid", False)),
        definition=str(payload.get("definition", "")).strip(),
        example=str(payload.get("example", "")).strip(),
    )

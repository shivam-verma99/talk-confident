"""Word list router — active / archived list management + live sentence generation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    ArchiveReason,
    Profile,
    WordListEntry,
    WordSource,
    WordStatus,
)
from app.deps import CurrentUser, DBSession, GenAIClient
from app.schemas.wordlist import (
    AddOwnWordRequest,
    GeneratedSentence,
    GenerateSentencesRequest,
    GenerateSentencesResponse,
    WordListEntryOut,
)
from app.services.ai_service import AIServiceError
from app.services.wordlist_service import (
    define_user_word,
    generate_sentences_for_words,
)

router = APIRouter(prefix="/words", tags=["words"])


# ---------------------------------------------------------------------------- helpers


async def _load_profile(db, user_id: uuid.UUID) -> Profile:
    return (
        await db.execute(select(Profile).where(Profile.user_id == user_id))
    ).scalar_one()


async def _load_entry(
    db, user_id: uuid.UUID, entry_id: uuid.UUID
) -> WordListEntry:
    entry = (
        await db.execute(
            select(WordListEntry).where(
                WordListEntry.id == entry_id, WordListEntry.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=404, detail="Word list entry not found.")
    return entry


# ---------------------------------------------------------------------------- GET /words/active


@router.get("/active", response_model=list[WordListEntryOut])
async def list_active_words(
    current_user: CurrentUser, db: DBSession
) -> list[WordListEntryOut]:
    """Return the user's active word list, ordered by need (priority * needs-work)."""
    stmt = select(WordListEntry).where(
        WordListEntry.user_id == current_user.id,
        WordListEntry.status == WordStatus.active,
    )
    rows = (await db.execute(stmt)).scalars().all()
    # Sort in Python so we can use the composite ordering used by sentence generation.
    rows = sorted(
        rows,
        key=lambda e: e.priority * max(0.05, 1.0 - e.mastery_score / 100.0),
        reverse=True,
    )
    return [WordListEntryOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------- GET /words/archived


@router.get("/archived", response_model=list[WordListEntryOut])
async def list_archived_words(
    current_user: CurrentUser, db: DBSession
) -> list[WordListEntryOut]:
    """Return archived words (mastered + denied + removed). Read-only on the client."""
    stmt = (
        select(WordListEntry)
        .where(
            WordListEntry.user_id == current_user.id,
            WordListEntry.status == WordStatus.archived,
        )
        .order_by(WordListEntry.archived_at.desc().nulls_last())
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [WordListEntryOut.model_validate(r) for r in rows]


# ---------------------------------------------------------------------------- POST /words/active


@router.post(
    "/active",
    response_model=WordListEntryOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_own_word(
    payload: AddOwnWordRequest,
    current_user: CurrentUser,
    db: DBSession,
    client: GenAIClient,
) -> WordListEntryOut:
    """User adds a word to their own list. Gemini fills in missing definition/example.

    User-added words receive priority 80 by default so personal goals surface near
    the top of the active list.
    """
    word = payload.word.strip()
    if not word:
        raise HTTPException(status_code=400, detail="Word cannot be empty.")

    # Duplicate guard.
    existing = (
        await db.execute(
            select(WordListEntry).where(
                WordListEntry.user_id == current_user.id,
                WordListEntry.word.ilike(word),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"'{word}' is already in your {existing.status.value} list.",
        )

    definition = (payload.definition or "").strip()
    example = (payload.example or "").strip() or None

    if not definition:
        profile = await _load_profile(db, current_user.id)
        try:
            result = await define_user_word(
                client=client,
                db=db,
                user=current_user,
                profile=profile,
                word=word,
            )
        except AIServiceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        if not result.valid or not result.definition:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"'{word}' does not look like a recognised English word. "
                    "Add a definition manually if you want to practice it anyway."
                ),
            )
        definition = result.definition
        if not example:
            example = result.example or None

    entry = WordListEntry(
        user_id=current_user.id,
        word=word,
        definition=definition,
        example=example,
        source=WordSource.user,
        status=WordStatus.active,
        priority=80.0,  # personal picks float near the top
    )
    db.add(entry)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="Word already exists for this user."
        ) from exc
    await db.commit()
    await db.refresh(entry)
    return WordListEntryOut.model_validate(entry)


# ---------------------------------------------------------------------------- DELETE /words/active/{id}


@router.delete(
    "/active/{entry_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_own_word(
    entry_id: uuid.UUID, current_user: CurrentUser, db: DBSession
) -> None:
    """Remove a *user-added* word. Coach-added words must use ``deny`` instead."""
    entry = await _load_entry(db, current_user.id, entry_id)
    if entry.source != WordSource.user:
        raise HTTPException(
            status_code=400,
            detail=(
                "Only words you added yourself can be removed. Use the deny "
                "action to archive a coach-suggested word."
            ),
        )
    if entry.status == WordStatus.archived:
        raise HTTPException(
            status_code=400, detail="Word is already archived."
        )
    # User-added words that are removed go to archived with reason=removed so
    # the audit trail is preserved and re-adds are blocked by the unique constraint.
    entry.status = WordStatus.archived
    entry.archive_reason = ArchiveReason.removed
    entry.archived_at = datetime.now(timezone.utc)
    await db.commit()


# ---------------------------------------------------------------------------- POST /words/active/{id}/deny


@router.post(
    "/active/{entry_id}/deny", response_model=WordListEntryOut
)
async def deny_coach_word(
    entry_id: uuid.UUID, current_user: CurrentUser, db: DBSession
) -> WordListEntryOut:
    """User declines a coach suggestion. Word is archived; AI will not re-suggest."""
    entry = await _load_entry(db, current_user.id, entry_id)
    if entry.source != WordSource.coach:
        raise HTTPException(
            status_code=400,
            detail="Only coach-suggested words can be denied. Use delete for your own words.",
        )
    if entry.status == WordStatus.archived:
        raise HTTPException(
            status_code=400, detail="Word is already archived."
        )
    entry.status = WordStatus.archived
    entry.archive_reason = ArchiveReason.denied
    entry.archived_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(entry)
    return WordListEntryOut.model_validate(entry)


# ---------------------------------------------------------------------------- POST /words/sentences


@router.post(
    "/sentences", response_model=GenerateSentencesResponse
)
async def generate_sentences(
    payload: GenerateSentencesRequest,
    current_user: CurrentUser,
    db: DBSession,
    client: GenAIClient,
) -> GenerateSentencesResponse:
    """Live-generate practice sentences for the user's selected (or auto-picked) words."""
    profile = await _load_profile(db, current_user.id)
    try:
        sentences, focus, reinforcement = await generate_sentences_for_words(
            client=client,
            db=db,
            user=current_user,
            profile=profile,
            explicit_word_ids=payload.word_ids,
            count=payload.count,
        )
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not sentences:
        raise HTTPException(
            status_code=409,
            detail=(
                "Your active word list is empty. Practice a few attempts first, or "
                "add a personal word, so we can tailor sentences for you."
            ),
        )

    await db.commit()
    return GenerateSentencesResponse(
        sentences=[GeneratedSentence.model_validate(s) for s in sentences],
        focus_words=focus,
        reinforcement_words=reinforcement,
    )

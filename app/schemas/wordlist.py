"""DTOs for the curated word list feature."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import ArchiveReason, WordSource, WordStatus


class WordListEntryOut(BaseModel):
    """One entry in the active or archived list."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    word: str
    definition: str
    example: str | None
    why_chosen: str | None
    target_weakness: str | None
    source: WordSource
    status: WordStatus
    archive_reason: ArchiveReason | None
    priority: float
    attempts_count: int
    mastery_score: float
    last_practiced_at: datetime | None
    created_at: datetime
    archived_at: datetime | None


class AddOwnWordRequest(BaseModel):
    """Body for user-initiated word addition.

    Definition is optional — if absent the backend asks Gemini to fill it in.
    """

    word: str = Field(..., min_length=1, max_length=128)
    definition: str | None = Field(default=None, max_length=2000)
    example: str | None = Field(default=None, max_length=2000)


class GenerateSentencesRequest(BaseModel):
    """Body for live sentence generation against selected active words."""

    word_ids: list[uuid.UUID] | None = Field(
        default=None,
        description=(
            "Active-list entry ids to focus on. If omitted, the top-N active "
            "words by priority * (1 - mastery_score/100) are used."
        ),
    )
    count: int = Field(default=6, ge=1, le=12)


class GeneratedSentence(BaseModel):
    text: str
    target_words: list[str]
    difficulty: int
    why_useful: str


class GenerateSentencesResponse(BaseModel):
    sentences: list[GeneratedSentence]
    focus_words: list[str]
    reinforcement_words: list[str]

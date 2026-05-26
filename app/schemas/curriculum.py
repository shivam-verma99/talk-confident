"""Curriculum DTOs."""

from __future__ import annotations

from pydantic import BaseModel, Field


class VocabItem(BaseModel):
    word: str
    definition: str
    example: str
    why_chosen: str = Field(
        ..., description="Brief justification — weakness or professional-need driven."
    )


class PracticeSentence(BaseModel):
    text: str
    target_words: list[str] = Field(default_factory=list)
    difficulty: int = Field(..., ge=1, le=10)


class MeetingSeed(BaseModel):
    title: str
    context: str
    role: str
    opening_line: str


class CurriculumResponse(BaseModel):
    level: int
    focus_areas: list[str]
    words: list[VocabItem]
    sentences: list[PracticeSentence]
    meeting_scenario_seed: MeetingSeed | None = None

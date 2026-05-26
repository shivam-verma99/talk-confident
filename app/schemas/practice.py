"""Practice and evaluation DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import PracticeMode, QualityBand


# ----------------------------------------------------- Evaluation result (Gemini)


class HesitationMarker(BaseModel):
    type: str = Field(..., description="filler / pause / restart / mispronunciation / other")
    timestamp_s: float = Field(..., ge=0)
    note: str


class Correction(BaseModel):
    issue: str
    fix: str
    example: str


class RecommendedDrill(BaseModel):
    name: str
    instructions: str


class EvaluationScores(BaseModel):
    pronunciation_clarity: int = Field(..., ge=0, le=100)
    fluency: int = Field(..., ge=0, le=100)
    confidence: int = Field(..., ge=0, le=100)
    grammar: int = Field(..., ge=0, le=100)


class EvaluationResult(BaseModel):
    """The structured JSON we expect back from Gemini."""

    transcript: str
    scores: EvaluationScores
    hesitation_markers: list[HesitationMarker] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    corrections: list[Correction] = Field(default_factory=list)
    recommended_drill: RecommendedDrill | None = None
    overall_note: str


# ----------------------------------------------------- Persisted attempt summary


class AttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID | None
    target_text: str | None
    transcript: str | None
    composite_score: int
    pronunciation_score: int
    fluency_score: int
    confidence_score: int
    grammar_score: int
    quality_band: QualityBand
    client_audio_ref: str | None
    duration_seconds: float | None
    mime_type: str | None
    created_at: datetime


class EvaluationResponse(BaseModel):
    """What the API returns after a single ``/practice/evaluate`` call."""

    attempt_id: uuid.UUID
    session_id: uuid.UUID | None
    composite_score: int
    quality_band: QualityBand
    evaluation: EvaluationResult


# ----------------------------------------------------- Meeting prep


class MeetingPrepStartRequest(BaseModel):
    focus_area: str | None = Field(
        default=None,
        description=(
            "Optional theme such as 'fiber_cut_restoration', 'vendor_escalation', "
            "'team_briefing'. If omitted, server picks based on recent weaknesses."
        ),
    )


class MeetingScenario(BaseModel):
    title: str
    context: str
    your_role: str
    counterpart_role: str
    objectives: list[str]
    opening_prompt: str
    suggested_phrases: list[str] = Field(default_factory=list)


class MeetingPrepStartResponse(BaseModel):
    session_id: uuid.UUID
    mode: PracticeMode = PracticeMode.meeting_prep
    scenario: MeetingScenario


class MeetingPrepTurnResponse(BaseModel):
    attempt_id: uuid.UUID
    session_id: uuid.UUID
    composite_score: int
    quality_band: QualityBand
    evaluation: EvaluationResult
    next_prompt: str

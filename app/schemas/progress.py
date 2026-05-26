"""Progress + level-up DTOs."""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.practice import EvaluationScores


class ScoreTrend(BaseModel):
    axis: str
    last_value: int
    rolling_avg: float
    delta_vs_prior_window: float


class LevelRecommendation(BaseModel):
    current_level: int
    should_level_up: bool
    confidence: float = Field(..., ge=0, le=1)
    reason: str
    evidence: list[str] = Field(default_factory=list)


class ProgressReport(BaseModel):
    current_level: int
    readiness_score: float
    attempts_count: int
    best_count: int
    worst_count: int
    rolling_scores: EvaluationScores
    top_weaknesses: list[str]
    top_strengths: list[str]
    trends: list[ScoreTrend]
    recommendation: LevelRecommendation

"""Progress aggregation, readiness threshold, and best/worst classification."""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PracticeAttempt, Profile, QualityBand
from app.schemas.practice import EvaluationResult, EvaluationScores
from app.schemas.progress import ScoreTrend


# Weighted composite. Pronunciation and fluency carry the heaviest weight, then
# confidence, then grammar (grammar is already strong per the user persona).
SCORE_WEIGHTS: dict[str, float] = {
    "pronunciation_clarity": 0.30,
    "fluency": 0.30,
    "confidence": 0.25,
    "grammar": 0.15,
}


def composite_from_scores(scores: EvaluationScores) -> int:
    """Compute the weighted composite score (0-100)."""
    raw = (
        SCORE_WEIGHTS["pronunciation_clarity"] * scores.pronunciation_clarity
        + SCORE_WEIGHTS["fluency"] * scores.fluency
        + SCORE_WEIGHTS["confidence"] * scores.confidence
        + SCORE_WEIGHTS["grammar"] * scores.grammar
    )
    return max(0, min(100, round(raw)))


# --------------------------------------------------------------------------- quality band


@dataclass(frozen=True)
class QualityClassification:
    composite_score: int
    band: QualityBand


async def classify_quality_band(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    new_scores: EvaluationScores,
    window: int = 30,
    delta_from_median: int = 8,
) -> QualityClassification:
    """Classify this attempt against the user's recent rolling distribution.

    ``best`` → composite is at or above the rolling p75 AND >= median + delta.
    ``worst`` → composite is at or below the rolling p25 AND <= median - delta.
    Otherwise ``neutral``. With < 5 prior attempts everything is ``neutral`` (not
    enough signal to call best/worst yet).
    """
    new_composite = composite_from_scores(new_scores)

    stmt = (
        select(PracticeAttempt.composite_score)
        .where(PracticeAttempt.user_id == user_id)
        .order_by(PracticeAttempt.created_at.desc())
        .limit(window)
    )
    result = await db.execute(stmt)
    prior = [row[0] for row in result.all()]

    if len(prior) < 5:
        return QualityClassification(composite_score=new_composite, band=QualityBand.neutral)

    series_sorted = sorted(prior)
    p25 = series_sorted[max(0, len(series_sorted) // 4 - 1)]
    p75 = series_sorted[min(len(series_sorted) - 1, (3 * len(series_sorted)) // 4)]
    median = statistics.median(series_sorted)

    if new_composite >= p75 and new_composite >= median + delta_from_median:
        return QualityClassification(composite_score=new_composite, band=QualityBand.best)
    if new_composite <= p25 and new_composite <= median - delta_from_median:
        return QualityClassification(composite_score=new_composite, band=QualityBand.worst)
    return QualityClassification(composite_score=new_composite, band=QualityBand.neutral)


# --------------------------------------------------------------------------- readiness


@dataclass(frozen=True)
class ReadinessSnapshot:
    rolling_scores: EvaluationScores
    readiness_score: float
    consistency: float


async def compute_readiness(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    window: int = 10,
) -> ReadinessSnapshot:
    """Aggregate the last ``window`` attempts into a single readiness snapshot."""
    stmt = (
        select(PracticeAttempt)
        .where(PracticeAttempt.user_id == user_id)
        .order_by(PracticeAttempt.created_at.desc())
        .limit(window)
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        zero = EvaluationScores(
            pronunciation_clarity=0, fluency=0, confidence=0, grammar=0
        )
        return ReadinessSnapshot(rolling_scores=zero, readiness_score=0.0, consistency=0.0)

    def avg(getter):
        return round(sum(getter(r) for r in rows) / len(rows))

    rolling = EvaluationScores(
        pronunciation_clarity=avg(lambda r: r.pronunciation_score),
        fluency=avg(lambda r: r.fluency_score),
        confidence=avg(lambda r: r.confidence_score),
        grammar=avg(lambda r: r.grammar_score),
    )
    composites = [r.composite_score for r in rows]
    avg_composite = sum(composites) / len(composites)
    stdev = statistics.pstdev(composites) if len(composites) > 1 else 0.0
    consistency = max(0.0, 1.0 - stdev / 50.0)  # 0..1, 1 = perfectly consistent
    readiness = round(min(100, avg_composite) * (0.7 + 0.3 * consistency), 2)
    return ReadinessSnapshot(
        rolling_scores=rolling, readiness_score=readiness, consistency=consistency
    )


# --------------------------------------------------------------------------- updates


async def apply_evaluation_to_profile(
    *,
    db: AsyncSession,
    profile: Profile,
    user_id: uuid.UUID,
) -> ReadinessSnapshot:
    """Refresh the profile's readiness fields after a new attempt is persisted."""
    snapshot = await compute_readiness(db=db, user_id=user_id)
    profile.readiness_score = snapshot.readiness_score
    await db.flush()
    return snapshot


# --------------------------------------------------------------------------- trends


async def compute_trends(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    window: int = 10,
) -> list[ScoreTrend]:
    """Compare the last ``window`` attempts to the prior ``window`` attempts per axis."""
    stmt = (
        select(PracticeAttempt)
        .where(PracticeAttempt.user_id == user_id)
        .order_by(PracticeAttempt.created_at.desc())
        .limit(window * 2)
    )
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return []

    recent = rows[:window]
    prior = rows[window : window * 2]

    def axis_avg(rows_, getter):
        return sum(getter(r) for r in rows_) / len(rows_) if rows_ else 0.0

    axes = {
        "pronunciation_clarity": lambda r: r.pronunciation_score,
        "fluency": lambda r: r.fluency_score,
        "confidence": lambda r: r.confidence_score,
        "grammar": lambda r: r.grammar_score,
    }

    trends: list[ScoreTrend] = []
    for axis_name, getter in axes.items():
        recent_avg = axis_avg(recent, getter)
        prior_avg = axis_avg(prior, getter)
        trends.append(
            ScoreTrend(
                axis=axis_name,
                last_value=int(getter(recent[0])),
                rolling_avg=round(recent_avg, 1),
                delta_vs_prior_window=round(recent_avg - prior_avg, 1),
            )
        )
    return trends


# --------------------------------------------------------------------------- counts


async def quality_band_counts(
    *, db: AsyncSession, user_id: uuid.UUID
) -> tuple[int, int, int]:
    """Return ``(total, best, worst)`` for this user."""
    total_stmt = select(func.count(PracticeAttempt.id)).where(
        PracticeAttempt.user_id == user_id
    )
    best_stmt = total_stmt.where(PracticeAttempt.quality_band == QualityBand.best)
    worst_stmt = total_stmt.where(PracticeAttempt.quality_band == QualityBand.worst)
    total = int((await db.execute(total_stmt)).scalar_one())
    best = int((await db.execute(best_stmt)).scalar_one())
    worst = int((await db.execute(worst_stmt)).scalar_one())
    return total, best, worst


# --------------------------------------------------------------------------- weakness mining


async def gather_recent_weakness_signals(
    *,
    db: AsyncSession,
    user_id: uuid.UUID,
    limit: int = 25,
) -> tuple[list[str], list[str]]:
    """Aggregate top weaknesses / strengths from recent attempts."""
    stmt = (
        select(PracticeAttempt.weaknesses_detected, PracticeAttempt.strengths_detected)
        .where(PracticeAttempt.user_id == user_id)
        .order_by(PracticeAttempt.created_at.desc())
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    weakness_counts: dict[str, int] = {}
    strength_counts: dict[str, int] = {}
    for w_list, s_list in rows:
        for w in (w_list or [])[:5]:
            weakness_counts[w] = weakness_counts.get(w, 0) + 1
        for s in (s_list or [])[:5]:
            strength_counts[s] = strength_counts.get(s, 0) + 1

    top_weak = sorted(weakness_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    top_strong = sorted(strength_counts.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return [w for w, _ in top_weak], [s for s, _ in top_strong]


def evaluation_to_score_dict(eval_result: EvaluationResult) -> dict[str, int]:
    """Flatten the four axes for persistence."""
    s = eval_result.scores
    return {
        "pronunciation_score": s.pronunciation_clarity,
        "fluency_score": s.fluency,
        "confidence_score": s.confidence,
        "grammar_score": s.grammar,
    }

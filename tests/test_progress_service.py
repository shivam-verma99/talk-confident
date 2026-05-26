"""Unit tests for progress_service — composite scoring and quality bands."""

from __future__ import annotations

import uuid

import pytest

from app.db.models import PracticeAttempt, Profile, QualityBand, User
from app.schemas.practice import EvaluationScores
from app.services.progress_service import (
    classify_quality_band,
    composite_from_scores,
    compute_readiness,
)


def test_composite_weights():
    scores = EvaluationScores(
        pronunciation_clarity=80, fluency=60, confidence=40, grammar=100
    )
    # 0.30*80 + 0.30*60 + 0.25*40 + 0.15*100 = 24 + 18 + 10 + 15 = 67
    assert composite_from_scores(scores) == 67


def test_composite_bounds():
    zeros = EvaluationScores(
        pronunciation_clarity=0, fluency=0, confidence=0, grammar=0
    )
    full = EvaluationScores(
        pronunciation_clarity=100, fluency=100, confidence=100, grammar=100
    )
    assert composite_from_scores(zeros) == 0
    assert composite_from_scores(full) == 100


async def _seed_user(session) -> uuid.UUID:
    user = User(google_sub=f"s-{uuid.uuid4()}", email=f"{uuid.uuid4()}@e.com", role="engineer")
    session.add(user)
    await session.flush()
    session.add(Profile(user_id=user.id))
    await session.flush()
    return user.id


async def _seed_attempts(session, user_id: uuid.UUID, composites: list[int]) -> None:
    for c in composites:
        session.add(
            PracticeAttempt(
                user_id=user_id,
                composite_score=c,
                pronunciation_score=c,
                fluency_score=c,
                confidence_score=c,
                grammar_score=c,
                quality_band=QualityBand.neutral,
                weaknesses_detected=[],
                strengths_detected=[],
                corrections=[],
                hesitation_markers=[],
            )
        )
    await session.commit()


@pytest.mark.asyncio
async def test_classify_quality_band_returns_neutral_for_few_samples(session_factory):
    async with session_factory() as session:
        uid = await _seed_user(session)
        await _seed_attempts(session, uid, [60, 65, 70])  # only 3
        new = EvaluationScores(
            pronunciation_clarity=90, fluency=90, confidence=90, grammar=90
        )
        result = await classify_quality_band(db=session, user_id=uid, new_scores=new)
        assert result.band == QualityBand.neutral


@pytest.mark.asyncio
async def test_classify_quality_band_detects_best(session_factory):
    async with session_factory() as session:
        uid = await _seed_user(session)
        await _seed_attempts(session, uid, [40, 45, 50, 55, 60, 50, 48, 52])
        excellent = EvaluationScores(
            pronunciation_clarity=88, fluency=88, confidence=88, grammar=88
        )
        result = await classify_quality_band(db=session, user_id=uid, new_scores=excellent)
        assert result.band == QualityBand.best
        assert result.composite_score == 88


@pytest.mark.asyncio
async def test_classify_quality_band_detects_worst(session_factory):
    async with session_factory() as session:
        uid = await _seed_user(session)
        await _seed_attempts(session, uid, [75, 78, 80, 82, 76, 79, 81, 77])
        poor = EvaluationScores(
            pronunciation_clarity=30, fluency=30, confidence=30, grammar=30
        )
        result = await classify_quality_band(db=session, user_id=uid, new_scores=poor)
        assert result.band == QualityBand.worst


@pytest.mark.asyncio
async def test_compute_readiness_empty(session_factory):
    async with session_factory() as session:
        uid = await _seed_user(session)
        snapshot = await compute_readiness(db=session, user_id=uid)
        assert snapshot.readiness_score == 0.0


@pytest.mark.asyncio
async def test_compute_readiness_with_attempts(session_factory):
    async with session_factory() as session:
        uid = await _seed_user(session)
        await _seed_attempts(session, uid, [70, 72, 71, 73, 70, 72, 71, 73, 72, 71])
        snapshot = await compute_readiness(db=session, user_id=uid)
        assert snapshot.rolling_scores.pronunciation_clarity == 72  # rounded
        assert snapshot.readiness_score > 60
        assert snapshot.consistency > 0.9

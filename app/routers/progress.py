"""User progress + level-up recommendation."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.db.models import Profile
from app.deps import CurrentUser, DBSession, GenAIClient
from app.schemas.progress import LevelRecommendation, ProgressReport
from app.services.ai_service import AIServiceError, recommend_level_up
from app.services.progress_service import (
    compute_readiness,
    compute_trends,
    gather_recent_weakness_signals,
    quality_band_counts,
)

router = APIRouter(prefix="/user", tags=["progress"])


@router.get("/progress", response_model=ProgressReport)
async def get_progress(
    current_user: CurrentUser,
    db: DBSession,
    client: GenAIClient,
) -> ProgressReport:
    """Return aggregated progress plus an AI-driven level-up recommendation."""
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == current_user.id))
    ).scalar_one()

    readiness = await compute_readiness(db=db, user_id=current_user.id)
    trends = await compute_trends(db=db, user_id=current_user.id)
    total, best, worst = await quality_band_counts(db=db, user_id=current_user.id)
    weaknesses, strengths = await gather_recent_weakness_signals(
        db=db, user_id=current_user.id
    )

    stats_payload = {
        "current_level": profile.current_level,
        "rolling_scores": readiness.rolling_scores.model_dump(),
        "readiness_score": readiness.readiness_score,
        "consistency": readiness.consistency,
        "attempts_count": total,
        "best_count": best,
        "worst_count": worst,
        "top_weaknesses": weaknesses,
        "top_strengths": strengths,
        "trends": [t.model_dump() for t in trends],
    }

    if total == 0:
        recommendation = LevelRecommendation(
            current_level=profile.current_level,
            should_level_up=False,
            confidence=0.0,
            reason="No practice attempts on record yet.",
            evidence=[],
        )
    else:
        try:
            recommendation = await recommend_level_up(
                client=client,
                db=db,
                user=current_user,
                profile=profile,
                rolling_stats=stats_payload,
            )
            await db.commit()
        except AIServiceError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Recommendation failed: {exc}",
            ) from exc

    return ProgressReport(
        current_level=profile.current_level,
        readiness_score=readiness.readiness_score,
        attempts_count=total,
        best_count=best,
        worst_count=worst,
        rolling_scores=readiness.rolling_scores,
        top_weaknesses=weaknesses,
        top_strengths=strengths,
        trends=trends,
        recommendation=recommendation,
    )

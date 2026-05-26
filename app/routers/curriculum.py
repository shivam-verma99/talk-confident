"""Curriculum router."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.db.models import Profile
from app.deps import CurrentUser, DBSession, GenAIClient
from app.schemas.curriculum import CurriculumResponse
from app.services.ai_service import AIServiceError, generate_daily_curriculum

router = APIRouter(prefix="/curriculum", tags=["curriculum"])


@router.get("/next", response_model=CurriculumResponse)
async def get_next_curriculum(
    current_user: CurrentUser,
    db: DBSession,
    client: GenAIClient,
    mode: str = Query(default="read_aloud", pattern="^(read_aloud|meeting_prep)$"),
) -> CurriculumResponse:
    """Return today's tailored vocabulary + practice sentences."""
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == current_user.id))
    ).scalar_one()

    try:
        curriculum = await generate_daily_curriculum(
            client=client,
            db=db,
            user=current_user,
            profile=profile,
            mode=mode,
        )
    except AIServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Curriculum generation failed: {exc}",
        ) from exc

    await db.commit()
    return curriculum

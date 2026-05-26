"""Practice router — evaluate, meeting prep, attempt listing."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.base import session_scope
from app.db.models import (
    PracticeAttempt,
    PracticeMode,
    PracticeSession,
    Profile,
    QualityBand,
    User,
)
from app.deps import CurrentUser, DBSession, GenAIClient
from app.schemas.practice import (
    AttemptOut,
    EvaluationResponse,
    MeetingPrepStartRequest,
    MeetingPrepStartResponse,
    MeetingPrepTurnResponse,
    MeetingScenario,
)
from app.services.ai_service import (
    AIServiceError,
    evaluate_audio_attempt,
    evaluate_meeting_turn,
    generate_meeting_scenario,
    summarize_history,
)
from app.services.audio_service import (
    AudioPreparationError,
    AudioValidationError,
    delete_gemini_file,
    prepare_audio_part,
    validate_audio_upload,
)
from app.services.progress_service import (
    apply_evaluation_to_profile,
    classify_quality_band,
    evaluation_to_score_dict,
)
from app.services.wordlist_service import (
    analyze_attempt_for_word_recommendations,
    apply_attempt_to_existing_words,
)

router = APIRouter(prefix="/practice", tags=["practice"])


# --------------------------------------------------------------------------- helpers


async def _read_upload(upload: UploadFile) -> tuple[bytes, str]:
    """Read the upload entirely into memory and validate it.

    Server holds zero audio at rest. The buffer is GC'd as soon as it leaves scope.
    """
    settings = get_settings()
    buffer = await upload.read(settings.max_audio_bytes + 1)
    if len(buffer) > settings.max_audio_bytes:
        raise AudioValidationError(
            f"Audio payload exceeds limit of {settings.max_audio_bytes} bytes."
        )
    mime = validate_audio_upload(mime_type=upload.content_type, size_bytes=len(buffer))
    return buffer, mime


async def _schedule_wordlist_analysis(
    *,
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> None:
    """Queue a Gemini call that may add new coach words after this attempt.

    The mastery updates for *already-active* words already happened inline
    (cheap DB-only). This task is the slow one — it consults Gemini to look
    for new patterns worth adding.
    """
    genai_client = request.app.state.genai_client

    async def _run() -> None:
        async with session_scope() as session:
            user = (
                await session.execute(select(User).where(User.id == user_id))
            ).scalar_one()
            profile = (
                await session.execute(select(Profile).where(Profile.user_id == user_id))
            ).scalar_one()
            attempt = (
                await session.execute(
                    select(PracticeAttempt).where(PracticeAttempt.id == attempt_id)
                )
            ).scalar_one()
            await analyze_attempt_for_word_recommendations(
                client=genai_client,
                db=session,
                user=user,
                profile=profile,
                attempt=attempt,
            )

    background_tasks.add_task(_run)


async def _maybe_schedule_summarization(
    *,
    request: Request,
    background_tasks: BackgroundTasks,
    profile: Profile,
    new_tokens: int,
) -> None:
    """Queue a summarization job when the running token cost crosses the threshold."""
    settings = get_settings()
    profile.compressed_token_count = (profile.compressed_token_count or 0) + new_tokens
    if profile.compressed_token_count < settings.context_token_threshold:
        return

    user_id = profile.user_id
    genai_client = request.app.state.genai_client

    async def _run() -> None:
        async with session_scope() as session:
            try:
                await summarize_history(client=genai_client, db=session, user_id=user_id)
            except Exception:  # pragma: no cover - background errors are logged elsewhere
                raise

    background_tasks.add_task(_run)


# --------------------------------------------------------------------------- /evaluate


@router.post(
    "/evaluate",
    response_model=EvaluationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def evaluate_practice(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    db: DBSession,
    client: GenAIClient,
    audio: UploadFile = File(..., description="Recorded audio in a supported audio/* MIME type."),
    target_text: Annotated[str | None, Form()] = None,
    mode: Annotated[str, Form()] = PracticeMode.read_aloud.value,
    client_audio_ref: Annotated[str | None, Form()] = None,
    duration_seconds: Annotated[float | None, Form()] = None,
    session_id: Annotated[uuid.UUID | None, Form()] = None,
) -> EvaluationResponse:
    """Evaluate one read-aloud or spontaneous practice attempt."""
    try:
        practice_mode = PracticeMode(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Unknown practice mode: {mode}") from exc

    if practice_mode == PracticeMode.meeting_prep:
        raise HTTPException(
            status_code=400,
            detail="Use /practice/meeting-prep/turn for meeting_prep mode.",
        )

    try:
        buffer, mime = await _read_upload(audio)
    except AudioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == current_user.id))
    ).scalar_one()

    # Optional session linkage.
    session: PracticeSession | None = None
    if session_id is not None:
        session = (
            await db.execute(
                select(PracticeSession).where(
                    PracticeSession.id == session_id,
                    PracticeSession.user_id == current_user.id,
                )
            )
        ).scalar_one_or_none()

    prepared = None
    try:
        try:
            prepared = await prepare_audio_part(client=client, buffer=buffer, mime_type=mime)
        except AudioPreparationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        try:
            outcome = await evaluate_audio_attempt(
                client=client,
                db=db,
                user=current_user,
                profile=profile,
                audio_part=prepared.part,
                target_text=target_text,
                mode=practice_mode.value,
            )
        except AIServiceError as exc:
            raise HTTPException(status_code=502, detail=f"Gemini evaluation failed: {exc}") from exc
    finally:
        if prepared is not None:
            await delete_gemini_file(client, prepared.gemini_file_name)
        del buffer  # release reference; GC frees it shortly

    classification = await classify_quality_band(
        db=db, user_id=current_user.id, new_scores=outcome.result.scores
    )

    attempt = PracticeAttempt(
        session_id=session.id if session else None,
        user_id=current_user.id,
        target_text=target_text,
        transcript=outcome.result.transcript,
        **evaluation_to_score_dict(outcome.result),
        composite_score=classification.composite_score,
        quality_band=classification.band,
        hesitation_markers=[h.model_dump() for h in outcome.result.hesitation_markers],
        weaknesses_detected=list(outcome.result.weaknesses),
        strengths_detected=list(outcome.result.strengths),
        corrections=[c.model_dump() for c in outcome.result.corrections],
        evaluator_feedback=outcome.result.overall_note,
        recommended_drill=outcome.result.recommended_drill.model_dump()
        if outcome.result.recommended_drill
        else None,
        gemini_response=outcome.raw_response,
        tokens_used=outcome.tokens_used,
        client_audio_ref=client_audio_ref,
        duration_seconds=duration_seconds,
        mime_type=mime,
    )
    db.add(attempt)
    await db.flush()

    await apply_evaluation_to_profile(db=db, profile=profile, user_id=current_user.id)
    # Inline: bump mastery + auto-archive any active words that appeared in target_text.
    await apply_attempt_to_existing_words(
        db=db, user_id=current_user.id, attempt=attempt
    )
    await _maybe_schedule_summarization(
        request=request,
        background_tasks=background_tasks,
        profile=profile,
        new_tokens=outcome.tokens_used,
    )
    attempt_id = attempt.id
    await db.commit()
    await db.refresh(attempt)

    # Background: ask Gemini whether this attempt reveals patterns worth adding
    # as new coach words. Runs after the HTTP response is sent.
    await _schedule_wordlist_analysis(
        request=request,
        background_tasks=background_tasks,
        user_id=current_user.id,
        attempt_id=attempt_id,
    )

    return EvaluationResponse(
        attempt_id=attempt.id,
        session_id=attempt.session_id,
        composite_score=attempt.composite_score,
        quality_band=attempt.quality_band,
        evaluation=outcome.result,
    )


# --------------------------------------------------------------------------- meeting prep start


@router.post(
    "/meeting-prep/start",
    response_model=MeetingPrepStartResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_meeting_prep(
    payload: MeetingPrepStartRequest,
    current_user: CurrentUser,
    db: DBSession,
    client: GenAIClient,
) -> MeetingPrepStartResponse:
    """Open a Meeting Prep session with a generated scenario."""
    profile = (
        await db.execute(select(Profile).where(Profile.user_id == current_user.id))
    ).scalar_one()

    try:
        scenario = await generate_meeting_scenario(
            client=client,
            db=db,
            user=current_user,
            profile=profile,
            focus_area=payload.focus_area,
        )
    except AIServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    session = PracticeSession(
        user_id=current_user.id,
        mode=PracticeMode.meeting_prep,
        scenario=scenario.title,
        scenario_meta=scenario.model_dump(),
    )
    db.add(session)
    await db.flush()
    await db.commit()
    await db.refresh(session)

    return MeetingPrepStartResponse(session_id=session.id, scenario=scenario)


# --------------------------------------------------------------------------- meeting prep turn


@router.post(
    "/meeting-prep/turn",
    response_model=MeetingPrepTurnResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_meeting_prep_turn(
    request: Request,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser,
    db: DBSession,
    client: GenAIClient,
    audio: UploadFile = File(...),
    session_id: Annotated[uuid.UUID, Form()] = ...,
    client_audio_ref: Annotated[str | None, Form()] = None,
    duration_seconds: Annotated[float | None, Form()] = None,
) -> MeetingPrepTurnResponse:
    """Submit a spoken turn inside an active Meeting Prep session."""
    session = (
        await db.execute(
            select(PracticeSession).where(
                PracticeSession.id == session_id,
                PracticeSession.user_id == current_user.id,
                PracticeSession.mode == PracticeMode.meeting_prep,
            )
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Meeting-prep session not found.")
    if session.ended_at is not None:
        raise HTTPException(status_code=400, detail="This meeting-prep session is closed.")

    if not session.scenario_meta:
        raise HTTPException(status_code=500, detail="Session is missing its scenario payload.")
    scenario = MeetingScenario.model_validate(session.scenario_meta)

    try:
        buffer, mime = await _read_upload(audio)
    except AudioValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    profile = (
        await db.execute(select(Profile).where(Profile.user_id == current_user.id))
    ).scalar_one()

    prepared = None
    try:
        try:
            prepared = await prepare_audio_part(client=client, buffer=buffer, mime_type=mime)
        except AudioPreparationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        try:
            outcome = await evaluate_meeting_turn(
                client=client,
                db=db,
                user=current_user,
                profile=profile,
                audio_part=prepared.part,
                scenario=scenario,
            )
        except AIServiceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        if prepared is not None:
            await delete_gemini_file(client, prepared.gemini_file_name)
        del buffer

    classification = await classify_quality_band(
        db=db, user_id=current_user.id, new_scores=outcome.result.scores
    )

    attempt = PracticeAttempt(
        session_id=session.id,
        user_id=current_user.id,
        target_text=scenario.opening_prompt,
        transcript=outcome.result.transcript,
        **evaluation_to_score_dict(outcome.result),
        composite_score=classification.composite_score,
        quality_band=classification.band,
        hesitation_markers=[h.model_dump() for h in outcome.result.hesitation_markers],
        weaknesses_detected=list(outcome.result.weaknesses),
        strengths_detected=list(outcome.result.strengths),
        corrections=[c.model_dump() for c in outcome.result.corrections],
        evaluator_feedback=outcome.result.overall_note,
        recommended_drill=outcome.result.recommended_drill.model_dump()
        if outcome.result.recommended_drill
        else None,
        gemini_response=outcome.raw_response,
        tokens_used=outcome.tokens_used,
        client_audio_ref=client_audio_ref,
        duration_seconds=duration_seconds,
        mime_type=mime,
    )
    db.add(attempt)
    await db.flush()

    await apply_evaluation_to_profile(db=db, profile=profile, user_id=current_user.id)
    await apply_attempt_to_existing_words(
        db=db, user_id=current_user.id, attempt=attempt
    )
    await _maybe_schedule_summarization(
        request=request,
        background_tasks=background_tasks,
        profile=profile,
        new_tokens=outcome.tokens_used,
    )
    attempt_id = attempt.id
    await db.commit()
    await db.refresh(attempt)

    await _schedule_wordlist_analysis(
        request=request,
        background_tasks=background_tasks,
        user_id=current_user.id,
        attempt_id=attempt_id,
    )

    return MeetingPrepTurnResponse(
        attempt_id=attempt.id,
        session_id=session.id,
        composite_score=attempt.composite_score,
        quality_band=attempt.quality_band,
        evaluation=outcome.result,
        next_prompt=outcome.next_prompt,
    )


# --------------------------------------------------------------------------- meeting prep close


@router.post(
    "/meeting-prep/{session_id}/close",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def close_meeting_prep(
    session_id: uuid.UUID,
    current_user: CurrentUser,
    db: DBSession,
) -> None:
    """Mark a Meeting Prep session as ended."""
    session = (
        await db.execute(
            select(PracticeSession).where(
                PracticeSession.id == session_id,
                PracticeSession.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found.")
    if session.ended_at is None:
        session.ended_at = datetime.now(timezone.utc)
        await db.commit()


# --------------------------------------------------------------------------- /attempts


@router.get("/attempts", response_model=list[AttemptOut])
async def list_attempts(
    current_user: CurrentUser,
    db: DBSession,
    band: str = Query(default="all", pattern="^(all|best|worst|neutral)$"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AttemptOut]:
    """List recent attempts so the Android app can curate its local audio library."""
    stmt = (
        select(PracticeAttempt)
        .where(PracticeAttempt.user_id == current_user.id)
        .order_by(PracticeAttempt.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if band != "all":
        stmt = stmt.where(PracticeAttempt.quality_band == QualityBand(band))

    rows = (await db.execute(stmt)).scalars().all()
    return [AttemptOut.model_validate(r) for r in rows]

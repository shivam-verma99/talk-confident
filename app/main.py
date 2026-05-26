"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from google import genai

from app.config import get_settings
from app.db.base import dispose_engine
from app.routers import auth, curriculum, practice, progress
from app.security import AuthError, GoogleIdTokenError
from app.services.ai_service import AIRateLimitError, AIServiceError
from app.services.audio_service import AudioPreparationError, AudioValidationError

logger = logging.getLogger(__name__)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)
    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY is empty — Gemini calls will fail until it is set.")
    app.state.genai_client = genai.Client(api_key=settings.gemini_api_key)
    logger.info("App %s starting (env=%s)", settings.app_name, settings.app_env)
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("App %s stopped", settings.app_name)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Talk Confident",
        description=(
            "Backend for a spoken-English coach designed for senior professionals. "
            "Powered by Gemini 2.5 Flash native audio. Audio bytes stay on the device."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Routers
    app.include_router(auth.router)
    app.include_router(curriculum.router)
    app.include_router(practice.router)
    app.include_router(progress.router)

    @app.get("/healthz", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- exception handlers
    @app.exception_handler(AudioValidationError)
    async def _on_audio_validation(_request, exc: AudioValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(AudioPreparationError)
    async def _on_audio_prep(_request, exc: AudioPreparationError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(AIRateLimitError)
    async def _on_rate_limit(_request, exc: AIRateLimitError) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={"detail": str(exc)},
            headers={"Retry-After": "10"},
        )

    @app.exception_handler(AIServiceError)
    async def _on_ai_error(_request, exc: AIServiceError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(GoogleIdTokenError)
    async def _on_google_error(_request, exc: GoogleIdTokenError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    @app.exception_handler(AuthError)
    async def _on_auth_error(_request, exc: AuthError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"detail": str(exc)})

    return app


app = create_app()

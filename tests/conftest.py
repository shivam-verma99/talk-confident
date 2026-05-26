"""Shared pytest fixtures.

Tests run against an in-memory SQLite database (the models use SA 2.0 portable
``Uuid`` and ``JSON`` types). Gemini and Google auth are fully mocked, so no
network or API keys are required.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from types import SimpleNamespace

import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Environment must be set BEFORE importing the app so Settings picks it up.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-test-secret")
os.environ.setdefault("GOOGLE_OAUTH_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GEMINI_API_KEY", "test-key")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("CORS_ORIGINS", "http://localhost")

from app import deps as app_deps  # noqa: E402
from app.db import base as db_base  # noqa: E402
from app.db.models import Profile, User  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security import GoogleIdentity, create_access_token  # noqa: E402


# --------------------------------------------------------------------------- DB


@pytest_asyncio.fixture
async def engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(db_base.Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(engine):
    return async_sessionmaker(bind=engine, expire_on_commit=False)


@pytest_asyncio.fixture
async def db_session(session_factory) -> AsyncIterator:
    async with session_factory() as session:
        yield session


# --------------------------------------------------------------------------- Gemini stub


class FakeGenAIClient:
    """Captures the last generate_content call and returns pre-baked responses."""

    def __init__(self) -> None:
        self.next_payloads: list[dict] = []
        self.last_call: dict | None = None
        self.models = SimpleNamespace(
            generate_content=self._generate_content,
            count_tokens=lambda **_: SimpleNamespace(total_tokens=10),
        )
        self.files = SimpleNamespace(
            upload=lambda **_: SimpleNamespace(name="files/test", uri="https://example/files/test"),
            get=lambda **_: SimpleNamespace(state=SimpleNamespace(name="ACTIVE")),
            delete=lambda **_: None,
        )
        self.caches = SimpleNamespace(
            create=lambda **_: SimpleNamespace(name="cachedContents/test"),
            delete=lambda **_: None,
        )

    def queue(self, payload: dict) -> None:
        self.next_payloads.append(payload)

    def _generate_content(self, *, model, contents, config):
        if not self.next_payloads:
            raise RuntimeError("FakeGenAIClient has no queued payloads.")
        payload = self.next_payloads.pop(0)
        self.last_call = {"model": model, "contents": contents, "config": config}
        text = json.dumps(payload)
        return SimpleNamespace(
            text=text,
            usage_metadata=SimpleNamespace(total_token_count=42),
        )


@pytest_asyncio.fixture
async def fake_genai() -> FakeGenAIClient:
    return FakeGenAIClient()


# --------------------------------------------------------------------------- app


@pytest_asyncio.fixture
async def client(session_factory, fake_genai) -> AsyncIterator[AsyncClient]:
    app = create_app()

    async def _override_get_db():
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[app_deps.get_db] = _override_get_db

    async with LifespanManager(app):
        app.state.genai_client = fake_genai
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


# --------------------------------------------------------------------------- auth


@pytest_asyncio.fixture
async def authed_user(session_factory):
    """Create a user + profile directly and return (user, jwt)."""
    async with session_factory() as session:
        user = User(
            google_sub="sub-test",
            email="father@example.com",
            full_name="Mr. Test Engineer",
            role="engineer",
        )
        session.add(user)
        await session.flush()
        session.add(Profile(user_id=user.id))
        await session.commit()
        await session.refresh(user)

    token = create_access_token(subject=user.id)
    return user, token


@pytest_asyncio.fixture
def patch_google(monkeypatch):
    async def fake_verify(_token: str) -> GoogleIdentity:
        return GoogleIdentity(
            sub="google-sub-123",
            email="father@example.com",
            email_verified=True,
            name="Mr. Test Engineer",
            picture="https://example.com/pic.jpg",
        )

    import app.routers.auth as auth_router

    monkeypatch.setattr(auth_router, "verify_google_id_token", fake_verify)
    return fake_verify

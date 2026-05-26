"""SQLAlchemy 2.x ORM models.

Audio bytes are deliberately NOT stored anywhere. The server only persists scores,
transcripts, and an opaque ``client_audio_ref`` supplied by the Android app so the
device can correlate evaluations with its locally stored audio files.

Types use SQLAlchemy's portable ``Uuid`` and ``JSON`` so the same models can be
exercised under SQLite (for unit tests) and Postgres (production). The JSON columns
upgrade to JSONB on Postgres via ``with_variant``.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def _json_col():
    """Portable JSON column that upgrades to JSONB on Postgres."""
    return JSON().with_variant(JSONB(), "postgresql")


# --------------------------------------------------------------------------- enums


class PracticeMode(str, enum.Enum):
    read_aloud = "read_aloud"
    spontaneous = "spontaneous"
    meeting_prep = "meeting_prep"


class VocabSource(str, enum.Enum):
    daily = "daily"
    weakness = "weakness"
    meeting = "meeting"


class QualityBand(str, enum.Enum):
    best = "best"
    worst = "worst"
    neutral = "neutral"


# --------------------------------------------------------------------------- models


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    picture_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    role: Mapped[str] = mapped_column(String(64), default="engineer", server_default="engineer")
    native_languages: Mapped[list[str]] = mapped_column(
        _json_col(), default=lambda: ["hi", "mwr"]
    )
    preferred_analogy_domains: Mapped[list[str]] = mapped_column(
        _json_col(), default=lambda: ["telecom", "networking"]
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    profile: Mapped["Profile"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    vocab_words: Mapped[list["VocabWord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["PracticeSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    attempts: Mapped[list["PracticeAttempt"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    summaries: Mapped[list["ContextSummary"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    current_level: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    readiness_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    level_history: Mapped[list[dict]] = mapped_column(_json_col(), default=list)
    compressed_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    compressed_token_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_summarized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active_cache_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    cache_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="profile")


class VocabWord(Base):
    __tablename__ = "vocab_words"
    __table_args__ = (UniqueConstraint("user_id", "word", name="uq_vocab_user_word"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    word: Mapped[str] = mapped_column(String(128), index=True)
    definition: Mapped[str] = mapped_column(Text)
    example: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[VocabSource] = mapped_column(
        SAEnum(VocabSource, name="vocab_source"), default=VocabSource.daily
    )
    introduced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    mastery_score: Mapped[float] = mapped_column(Float, default=0.0, server_default="0")
    times_practiced: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    user: Mapped[User] = relationship(back_populates="vocab_words")


class PracticeSession(Base):
    __tablename__ = "practice_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    mode: Mapped[PracticeMode] = mapped_column(SAEnum(PracticeMode, name="practice_mode"))
    scenario: Mapped[str | None] = mapped_column(Text, nullable=True)
    scenario_meta: Mapped[dict | None] = mapped_column(_json_col(), nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")
    attempts: Mapped[list["PracticeAttempt"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class PracticeAttempt(Base):
    __tablename__ = "practice_attempts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("practice_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )

    target_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)

    pronunciation_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    fluency_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    confidence_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    grammar_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    composite_score: Mapped[int] = mapped_column(Integer, default=0, server_default="0", index=True)

    quality_band: Mapped[QualityBand] = mapped_column(
        SAEnum(QualityBand, name="quality_band"),
        default=QualityBand.neutral,
        server_default=QualityBand.neutral.value,
        index=True,
    )

    hesitation_markers: Mapped[list[dict]] = mapped_column(_json_col(), default=list)
    weaknesses_detected: Mapped[list[str]] = mapped_column(_json_col(), default=list)
    strengths_detected: Mapped[list[str]] = mapped_column(_json_col(), default=list)
    corrections: Mapped[list[dict]] = mapped_column(_json_col(), default=list)
    evaluator_feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    recommended_drill: Mapped[dict | None] = mapped_column(_json_col(), nullable=True)

    gemini_response: Mapped[dict | None] = mapped_column(_json_col(), nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Opaque device-local file id from the Android app. Server never stores audio bytes.
    client_audio_ref: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(8, 3), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    user: Mapped[User] = relationship(back_populates="attempts")
    session: Mapped[PracticeSession | None] = relationship(back_populates="attempts")


class ContextSummary(Base):
    __tablename__ = "context_summaries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    summary_text: Mapped[str] = mapped_column(Text)
    summary_json: Mapped[dict] = mapped_column(_json_col(), default=dict)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="summaries")

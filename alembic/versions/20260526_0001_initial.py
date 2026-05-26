"""Initial schema for Talk Confident.

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    practice_mode = sa.Enum(
        "read_aloud", "spontaneous", "meeting_prep", name="practice_mode"
    )
    vocab_source = sa.Enum("daily", "weakness", "meeting", name="vocab_source")
    quality_band = sa.Enum("best", "worst", "neutral", name="quality_band")
    practice_mode.create(op.get_bind(), checkfirst=True)
    vocab_source.create(op.get_bind(), checkfirst=True)
    quality_band.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("google_sub", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("picture_url", sa.String(1024), nullable=True),
        sa.Column("role", sa.String(64), nullable=False, server_default="engineer"),
        sa.Column(
            "native_languages",
            postgresql.JSONB,
            nullable=False,
            server_default='["hi","mwr"]',
        ),
        sa.Column(
            "preferred_analogy_domains",
            postgresql.JSONB,
            nullable=False,
            server_default='["telecom","networking"]',
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("google_sub", name="uq_users_google_sub"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_google_sub", "users", ["google_sub"])
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("current_level", sa.Integer, nullable=False, server_default="1"),
        sa.Column("readiness_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("level_history", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("compressed_history", sa.Text, nullable=True),
        sa.Column("compressed_token_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_summarized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_cache_name", sa.String(512), nullable=True),
        sa.Column("cache_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", name="uq_profiles_user_id"),
    )
    op.create_index("ix_profiles_user_id", "profiles", ["user_id"])

    op.create_table(
        "vocab_words",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("word", sa.String(128), nullable=False),
        sa.Column("definition", sa.Text, nullable=False),
        sa.Column("example", sa.Text, nullable=True),
        sa.Column("source", vocab_source, nullable=False, server_default="daily"),
        sa.Column("introduced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("mastery_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("times_practiced", sa.Integer, nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("user_id", "word", name="uq_vocab_user_word"),
    )
    op.create_index("ix_vocab_words_user_id", "vocab_words", ["user_id"])
    op.create_index("ix_vocab_words_word", "vocab_words", ["word"])

    op.create_table(
        "practice_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mode", practice_mode, nullable=False),
        sa.Column("scenario", sa.Text, nullable=True),
        sa.Column("scenario_meta", postgresql.JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_practice_sessions_user_id", "practice_sessions", ["user_id"])

    op.create_table(
        "practice_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_text", sa.Text, nullable=True),
        sa.Column("transcript", sa.Text, nullable=True),
        sa.Column("pronunciation_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("fluency_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("grammar_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("composite_score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("quality_band", quality_band, nullable=False, server_default="neutral"),
        sa.Column("hesitation_markers", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("weaknesses_detected", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("strengths_detected", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("corrections", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("evaluator_feedback", sa.Text, nullable=True),
        sa.Column("recommended_drill", postgresql.JSONB, nullable=True),
        sa.Column("gemini_response", postgresql.JSONB, nullable=True),
        sa.Column("tokens_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("client_audio_ref", sa.String(255), nullable=True),
        sa.Column("duration_seconds", sa.Numeric(8, 3), nullable=True),
        sa.Column("mime_type", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["practice_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_practice_attempts_user_id", "practice_attempts", ["user_id"])
    op.create_index("ix_practice_attempts_session_id", "practice_attempts", ["session_id"])
    op.create_index("ix_practice_attempts_quality_band", "practice_attempts", ["quality_band"])
    op.create_index("ix_practice_attempts_composite_score", "practice_attempts", ["composite_score"])
    op.create_index("ix_practice_attempts_created_at", "practice_attempts", ["created_at"])
    op.create_index("ix_practice_attempts_client_audio_ref", "practice_attempts", ["client_audio_ref"])

    op.create_table(
        "context_summaries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary_text", sa.Text, nullable=False),
        sa.Column("summary_json", postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("token_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_context_summaries_user_id", "context_summaries", ["user_id"])
    op.create_index("ix_context_summaries_is_active", "context_summaries", ["is_active"])


def downgrade() -> None:
    op.drop_index("ix_context_summaries_is_active", table_name="context_summaries")
    op.drop_index("ix_context_summaries_user_id", table_name="context_summaries")
    op.drop_table("context_summaries")

    for ix in (
        "ix_practice_attempts_client_audio_ref",
        "ix_practice_attempts_created_at",
        "ix_practice_attempts_composite_score",
        "ix_practice_attempts_quality_band",
        "ix_practice_attempts_session_id",
        "ix_practice_attempts_user_id",
    ):
        op.drop_index(ix, table_name="practice_attempts")
    op.drop_table("practice_attempts")

    op.drop_index("ix_practice_sessions_user_id", table_name="practice_sessions")
    op.drop_table("practice_sessions")

    op.drop_index("ix_vocab_words_word", table_name="vocab_words")
    op.drop_index("ix_vocab_words_user_id", table_name="vocab_words")
    op.drop_table("vocab_words")

    op.drop_index("ix_profiles_user_id", table_name="profiles")
    op.drop_table("profiles")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_index("ix_users_google_sub", table_name="users")
    op.drop_table("users")

    sa.Enum(name="quality_band").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="vocab_source").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="practice_mode").drop(op.get_bind(), checkfirst=True)

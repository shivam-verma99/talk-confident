"""Add user pronouns + curated word list entries.

Revision ID: 0002_pronouns_and_wordlist
Revises: 0001_initial
Create Date: 2026-05-27

Adds a nullable ``pronouns`` column to ``users`` and a new ``word_list_entries`` table
backing the curated active / archived word lists feature.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_pronouns_and_wordlist"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---------------- users.pronouns
    op.add_column(
        "users",
        sa.Column("pronouns", sa.String(64), nullable=True),
    )

    # ---------------- enums for word list entries
    word_source = postgresql.ENUM("coach", "user", name="word_source")
    word_status = postgresql.ENUM("active", "archived", name="word_status")
    archive_reason = postgresql.ENUM(
        "mastered", "denied", "removed", name="archive_reason"
    )
    word_source.create(op.get_bind(), checkfirst=True)
    word_status.create(op.get_bind(), checkfirst=True)
    archive_reason.create(op.get_bind(), checkfirst=True)

    word_source = postgresql.ENUM(
        "coach", "user", name="word_source", create_type=False
    )
    word_status = postgresql.ENUM(
        "active", "archived", name="word_status", create_type=False
    )
    archive_reason = postgresql.ENUM(
        "mastered", "denied", "removed", name="archive_reason", create_type=False
    )

    op.create_table(
        "word_list_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("word", sa.String(128), nullable=False),
        sa.Column("definition", sa.Text, nullable=False),
        sa.Column("example", sa.Text, nullable=True),
        sa.Column("why_chosen", sa.Text, nullable=True),
        sa.Column("target_weakness", sa.String(255), nullable=True),
        sa.Column("source", word_source, nullable=False, server_default="coach"),
        sa.Column("status", word_status, nullable=False, server_default="active"),
        sa.Column("archive_reason", archive_reason, nullable=True),
        sa.Column("priority", sa.Float, nullable=False, server_default="50.0"),
        sa.Column("attempts_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("mastery_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("last_practiced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "user_id", "word", name="uq_word_list_entries_user_word"
        ),
    )
    op.create_index("ix_word_list_entries_user_id", "word_list_entries", ["user_id"])
    op.create_index("ix_word_list_entries_word", "word_list_entries", ["word"])
    op.create_index("ix_word_list_entries_status", "word_list_entries", ["status"])
    op.create_index("ix_word_list_entries_source", "word_list_entries", ["source"])
    op.create_index(
        "ix_word_list_entries_created_at", "word_list_entries", ["created_at"]
    )


def downgrade() -> None:
    for ix in (
        "ix_word_list_entries_created_at",
        "ix_word_list_entries_source",
        "ix_word_list_entries_status",
        "ix_word_list_entries_word",
        "ix_word_list_entries_user_id",
    ):
        op.drop_index(ix, table_name="word_list_entries")
    op.drop_table("word_list_entries")

    sa.Enum(name="archive_reason").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="word_status").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="word_source").drop(op.get_bind(), checkfirst=True)

    op.drop_column("users", "pronouns")

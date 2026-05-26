"""Pure unit tests for prompt builders."""

from __future__ import annotations

from app.db.models import User
from app.prompts.persona import build_persona_instruction


def test_persona_uses_user_name():
    user = User(
        google_sub="x",
        email="x@y.com",
        full_name="Ramesh Sharma",
        role="engineer",
    )
    instruction = build_persona_instruction(user, compressed_profile=None)
    assert "Mr. Ramesh Sharma" in instruction
    assert "Divisional Engineer" in instruction
    assert "No prior history" in instruction  # default for fresh users
    assert "telecom" in instruction.lower()


def test_persona_includes_compressed_profile():
    user = User(google_sub="x", email="x@y.com", full_name="Mr. Ramesh", role="engineer")
    instruction = build_persona_instruction(
        user,
        compressed_profile="Persistent: th-sound substitution (high), filler 'uh' (medium).",
    )
    assert "th-sound substitution" in instruction

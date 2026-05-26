"""Tests for /user/progress."""

from __future__ import annotations

import pytest


LEVEL_UP_PAYLOAD = {
    "current_level": 1,
    "should_level_up": False,
    "confidence": 0.55,
    "reason": "Consistency improving but rolling average still below 65.",
    "evidence": [
        "10-attempt rolling average composite = 58",
        "th-sound substitution still appears in 6 of last 10 attempts",
    ],
}


@pytest.mark.asyncio
async def test_progress_empty_user_returns_neutral_recommendation(
    client, patch_google, fake_genai
):
    login = await client.post("/auth/google", json={"id_token": "fake"})
    token = login.json()["access_token"]

    r = await client.get(
        "/user/progress",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["attempts_count"] == 0
    assert body["recommendation"]["should_level_up"] is False
    assert body["recommendation"]["reason"].startswith("No practice")

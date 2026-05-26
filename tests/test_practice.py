"""Integration tests for practice/evaluate + best-worst surfacing."""

from __future__ import annotations

import io

import pytest


EVALUATION_PAYLOAD = {
    "transcript": "We need to escalate this to the vendor.",
    "scores": {
        "pronunciation_clarity": 72,
        "fluency": 68,
        "confidence": 65,
        "grammar": 80,
    },
    "hesitation_markers": [
        {"type": "filler", "timestamp_s": 1.4, "note": "uh before 'vendor'"}
    ],
    "weaknesses": ["th-sound substitution in 'this'"],
    "strengths": ["clear sentence-final intonation"],
    "corrections": [
        {
            "issue": "'this' pronounced as 'dis'",
            "fix": "Bite tongue lightly for /th/ sound.",
            "example": "Place 'this' on your tongue.",
        }
    ],
    "recommended_drill": {
        "name": "th-sound minimal pairs",
        "instructions": "Practise 'this/dis', 'three/tree' aloud 10 times.",
    },
    "overall_note": "Steady pace, control filler words.",
}


def _audio_upload() -> dict:
    return {
        "audio": ("clip.wav", io.BytesIO(b"\x00" * 4096), "audio/wav"),
    }


@pytest.mark.asyncio
async def test_evaluate_persists_attempt_and_classifies(client, patch_google, fake_genai):
    login = await client.post("/auth/google", json={"id_token": "fake"})
    token = login.json()["access_token"]

    fake_genai.queue(EVALUATION_PAYLOAD)

    r = await client.post(
        "/practice/evaluate",
        headers={"Authorization": f"Bearer {token}"},
        files=_audio_upload(),
        data={
            "target_text": "We need to escalate this to the vendor.",
            "mode": "read_aloud",
            "client_audio_ref": "local-001",
            "duration_seconds": "4.2",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["composite_score"] > 0
    assert body["evaluation"]["transcript"].startswith("We need")
    assert body["quality_band"] == "neutral"  # < 5 prior samples

    # And the attempt shows up in /attempts
    listing = await client.get(
        "/practice/attempts", headers={"Authorization": f"Bearer {token}"}
    )
    assert listing.status_code == 200
    attempts = listing.json()
    assert len(attempts) == 1
    assert attempts[0]["client_audio_ref"] == "local-001"


@pytest.mark.asyncio
async def test_evaluate_rejects_bad_mime(client, patch_google):
    login = await client.post("/auth/google", json={"id_token": "fake"})
    token = login.json()["access_token"]

    r = await client.post(
        "/practice/evaluate",
        headers={"Authorization": f"Bearer {token}"},
        files={"audio": ("x.bin", io.BytesIO(b"\x00" * 16), "application/octet-stream")},
        data={"mode": "read_aloud"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_evaluate_rejects_meeting_prep_mode_here(client, patch_google):
    login = await client.post("/auth/google", json={"id_token": "fake"})
    token = login.json()["access_token"]

    r = await client.post(
        "/practice/evaluate",
        headers={"Authorization": f"Bearer {token}"},
        files=_audio_upload(),
        data={"mode": "meeting_prep"},
    )
    assert r.status_code == 400

"""Integration tests for the curriculum router."""

from __future__ import annotations

import pytest


CURRICULUM_PAYLOAD = {
    "level": 3,
    "focus_areas": ["fluency under pressure", "th-sound clarity"],
    "words": [
        {
            "word": "escalate",
            "definition": "raise an issue to higher authority",
            "example": "We need to escalate this fiber-cut to the zonal office.",
            "why_chosen": "vendor escalation phrasing weakness",
        },
        {
            "word": "downtime",
            "definition": "period when a system is non-operational",
            "example": "Downtime on the OFC route exceeded SLA.",
            "why_chosen": "professional vocabulary need",
        },
        {
            "word": "provision",
            "definition": "make resources available for use",
            "example": "Provision an alternate route immediately.",
            "why_chosen": "active-voice practice",
        },
        {
            "word": "restoration",
            "definition": "act of bringing back to working order",
            "example": "Restoration time was 45 minutes.",
            "why_chosen": "fluency anchor word",
        },
        {
            "word": "outage",
            "definition": "a period of interrupted service",
            "example": "The outage affected three exchanges.",
            "why_chosen": "professional vocabulary",
        },
    ],
    "sentences": [
        {"text": "We restored the fiber link within thirty minutes.", "target_words": ["restoration"], "difficulty": 3},
        {"text": "I will escalate this to the vendor immediately.", "target_words": ["escalate"], "difficulty": 4},
        {"text": "Downtime on the Bikaner-Jaipur OFC route is unacceptable.", "target_words": ["downtime"], "difficulty": 5},
        {"text": "Please provision a backup link before noon.", "target_words": ["provision"], "difficulty": 3},
        {"text": "The outage report has been submitted.", "target_words": ["outage"], "difficulty": 2},
        {"text": "We tracked the cause to a backhaul failure.", "target_words": [], "difficulty": 4},
    ],
}


@pytest.mark.asyncio
async def test_curriculum_returns_payload(client, patch_google, fake_genai):
    login = await client.post("/auth/google", json={"id_token": "fake"})
    token = login.json()["access_token"]

    fake_genai.queue(CURRICULUM_PAYLOAD)

    r = await client.get(
        "/curriculum/next",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["level"] == 3
    assert len(body["words"]) == 5
    assert any("escalate" in w["word"] for w in body["words"])


@pytest.mark.asyncio
async def test_curriculum_requires_auth(client):
    r = await client.get("/curriculum/next")
    assert r.status_code == 401

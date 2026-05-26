"""Meeting Prep mode tests."""

from __future__ import annotations

import io

import pytest


SCENARIO_PAYLOAD = {
    "title": "Fiber-cut restoration vendor escalation",
    "context": (
        "Late-evening fiber cut between Bikaner exchange and Jaipur junction. "
        "Vendor has been on-site 45 minutes with no ETA. SLA breach in 30 minutes."
    ),
    "your_role": "Divisional Engineer (Maintenance)",
    "counterpart_role": "Vendor Field Engineer",
    "objectives": [
        "Get a concrete restoration ETA.",
        "Confirm spare splicing inventory.",
        "Document the cause for the morning briefing.",
    ],
    "opening_prompt": "Sir, we are still pulling the new patch. Can you confirm the route diversion?",
    "suggested_phrases": [
        "Please give me a concrete ETA.",
        "What is your current blocker?",
        "Document the root cause in writing.",
    ],
}


TURN_PAYLOAD = {
    "transcript": "Yes, route diversion is approved. Please share the ETA.",
    "scores": {
        "pronunciation_clarity": 70,
        "fluency": 66,
        "confidence": 62,
        "grammar": 78,
    },
    "hesitation_markers": [],
    "weaknesses": ["soft sentence endings"],
    "strengths": ["calm tone"],
    "corrections": [
        {
            "issue": "Trailing voice on 'ETA'",
            "fix": "Land 'ETA' with downward intonation.",
            "example": "Share the ETA.",
        }
    ],
    "recommended_drill": {
        "name": "End-of-sentence pitch drop",
        "instructions": "Read 5 sentences ending with a noun, dropping pitch on the last syllable.",
    },
    "overall_note": "Clear ask, but voice fades at the end.",
    "next_prompt": "ETA is 20 minutes once the splicing kit arrives.",
}


@pytest.mark.asyncio
async def test_meeting_prep_flow(client, patch_google, fake_genai):
    login = await client.post("/auth/google", json={"id_token": "fake"})
    token = login.json()["access_token"]

    # Start: queue the scenario.
    fake_genai.queue(SCENARIO_PAYLOAD)
    start = await client.post(
        "/practice/meeting-prep/start",
        headers={"Authorization": f"Bearer {token}"},
        json={"focus_area": "fiber_cut_restoration"},
    )
    assert start.status_code == 201, start.text
    session_id = start.json()["session_id"]
    assert start.json()["scenario"]["title"].startswith("Fiber-cut")

    # Turn: queue the turn evaluation + counterpart reply.
    fake_genai.queue(TURN_PAYLOAD)
    turn = await client.post(
        "/practice/meeting-prep/turn",
        headers={"Authorization": f"Bearer {token}"},
        files={"audio": ("turn.wav", io.BytesIO(b"\x00" * 4096), "audio/wav")},
        data={"session_id": session_id, "client_audio_ref": "local-turn-1"},
    )
    assert turn.status_code == 201, turn.text
    body = turn.json()
    assert body["next_prompt"].startswith("ETA")
    assert body["evaluation"]["transcript"].startswith("Yes")

    # Close it.
    close = await client.post(
        f"/practice/meeting-prep/{session_id}/close",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert close.status_code == 204

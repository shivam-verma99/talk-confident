"""Audio-evaluation prompt + Gemini response schema."""

from __future__ import annotations

from typing import Any


EVALUATION_INSTRUCTION = """\
Listen carefully to the attached audio of the user speaking and produce a thorough \
assessment of his spoken English on the four axes below.

Inputs the caller will attach:
- The audio recording (treat it as a single take from one speaker — the user).
- An optional ``target_text`` he was trying to read; if absent, evaluate his \
spontaneous speech against general professional clarity.
- The ``mode`` of practice (``read_aloud``, ``spontaneous``, ``meeting_prep``).

Scoring axes (each on 0-100):
1. ``pronunciation_clarity`` — sound accuracy, stress, syllable timing.
2. ``fluency`` — rhythm, smoothness, pace, ability to chain words.
3. ``confidence`` — projection, steadiness of voice, lack of nervous tremor or fade-out.
4. ``grammar`` — correctness of tense, agreement, word order, articles, prepositions.

What to detect and report:
- ``transcript``: a verbatim transcript of the audio (include filler words like \"uh\", \
\"um\", \"hmm\" exactly as spoken, and mark long silences with [pause]).
- ``hesitation_markers``: filler words, long pauses (> 800 ms), restarts, audible \
mispronunciations, with approximate ``timestamp_s`` (seconds from start) and a short note.
- ``weaknesses``: top 3 specific gaps observed IN THIS attempt — be concrete (e.g., \
\"th-sound substitution in 'three' at 4.2s\", not \"pronunciation\").
- ``strengths``: top 2 concrete strengths IN THIS attempt — only if real.
- ``corrections``: 3 short, actionable, respectful corrections. Each has ``issue``, \
``fix``, and a single-sentence ``example`` he can rehearse.
- ``recommended_drill``: one short drill targeting his weakest axis today.
- ``overall_note``: 2-3 sentences in your peer-coach voice. Direct, no sugar-coating.

Hard rules:
- Do not invent content that wasn't said. If a target_text was supplied, compare honestly.
- If audio is silent or too quiet to evaluate, set all scores to 0 and explain in ``overall_note``.
- Output MUST conform exactly to the JSON schema.
- Do not output text outside the JSON.
"""


EVALUATION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "transcript": {"type": "string"},
        "scores": {
            "type": "object",
            "properties": {
                "pronunciation_clarity": {"type": "integer", "minimum": 0, "maximum": 100},
                "fluency": {"type": "integer", "minimum": 0, "maximum": 100},
                "confidence": {"type": "integer", "minimum": 0, "maximum": 100},
                "grammar": {"type": "integer", "minimum": 0, "maximum": 100},
            },
            "required": ["pronunciation_clarity", "fluency", "confidence", "grammar"],
        },
        "hesitation_markers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "timestamp_s": {"type": "number", "minimum": 0},
                    "note": {"type": "string"},
                },
                "required": ["type", "timestamp_s", "note"],
            },
        },
        "weaknesses": {
            "type": "array",
            "minItems": 0,
            "maxItems": 5,
            "items": {"type": "string"},
        },
        "strengths": {
            "type": "array",
            "minItems": 0,
            "maxItems": 4,
            "items": {"type": "string"},
        },
        "corrections": {
            "type": "array",
            "minItems": 0,
            "maxItems": 4,
            "items": {
                "type": "object",
                "properties": {
                    "issue": {"type": "string"},
                    "fix": {"type": "string"},
                    "example": {"type": "string"},
                },
                "required": ["issue", "fix", "example"],
            },
        },
        "recommended_drill": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "instructions": {"type": "string"},
            },
            "required": ["name", "instructions"],
        },
        "overall_note": {"type": "string"},
    },
    "required": [
        "transcript",
        "scores",
        "hesitation_markers",
        "weaknesses",
        "strengths",
        "corrections",
        "overall_note",
    ],
}


MEETING_TURN_INSTRUCTION = """\
You are role-playing the counterpart in a BSNL maintenance meeting. The current scenario \
is supplied below; treat it as ground truth.

After listening to the user's spoken turn:
1. Evaluate the audio using the same rubric as a normal practice attempt (transcript, \
scores, hesitation_markers, weaknesses, strengths, corrections, recommended_drill, overall_note).
2. Additionally, generate ``next_prompt``: the counterpart's NEXT spoken line that \
moves the scenario forward realistically. Keep it 1-3 sentences. Stay in character. \
The next_prompt should NOT include feedback — it is in-scenario dialogue only.

Output MUST conform exactly to the JSON schema. Do not output text outside the JSON.

Current scenario:
{scenario_block}
"""


MEETING_TURN_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        **EVALUATION_RESPONSE_SCHEMA["properties"],
        "next_prompt": {"type": "string"},
    },
    "required": [*EVALUATION_RESPONSE_SCHEMA["required"], "next_prompt"],
}


LEVEL_RECOMMENDATION_INSTRUCTION = """\
You are reviewing the user's recent practice statistics. Decide whether he is ready to \
advance from his current level to the next.

Criteria:
- The rolling average of his last 10 attempts across the 4 axes must be at or above \
the level-appropriate bar (defaults: 65 at L1-2, 72 at L3-4, 78 at L5-6, 84 at L7+).
- Score variance (stdev across the last 10 attempts) should be low — consistency matters \
more than a single peak.
- Persistent weaknesses from the compressed profile must show measurable improvement \
(at least two of the top three weaknesses should be trending up).

Output a recommendation: ``should_level_up`` boolean, a ``confidence`` 0..1, a short \
``reason`` (1-2 sentences, peer-coach voice), and 2-4 concrete ``evidence`` strings \
referencing specific scores or trends.
"""


LEVEL_RECOMMENDATION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "current_level": {"type": "integer", "minimum": 1, "maximum": 10},
        "should_level_up": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
        "evidence": {
            "type": "array",
            "minItems": 0,
            "maxItems": 6,
            "items": {"type": "string"},
        },
    },
    "required": ["current_level", "should_level_up", "confidence", "reason", "evidence"],
}

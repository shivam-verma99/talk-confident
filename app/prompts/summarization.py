"""History-summarization prompt + Gemini response schema.

Triggered when the running token cost for a user crosses ``CONTEXT_TOKEN_THRESHOLD``.
The output replaces the previous active summary and the user's cached content is rotated.
"""

from __future__ import annotations

from typing import Any


SUMMARIZATION_INSTRUCTION = """\
You are compressing this user's learning history into a compact profile so it can be \
embedded in future system prompts via Gemini Explicit Context Caching.

Inputs you will receive:
- The previous compressed summary (if any).
- A batch of recent practice attempts: their target text, transcript, scores per axis, \
detected weaknesses, strengths, and corrections issued.

Compression rules (preserve detail where it matters, collapse where it doesn't):
1. Retain in FULL detail any weakness that has appeared in 3 or more of the last 20 \
attempts, OR in 5 consecutive recent attempts. Note severity (low / medium / high) and \
the last time it was observed.
2. Collapse mastered items (axis or vocab consistently scoring >= 85) into one-liners.
3. Rank the top 5 ongoing focus areas the coach should keep targeting.
4. Record his current level, last notable milestones (e.g., \"first attempt above 80 on \
fluency, 2026-05-12\"), and 2-4 recommended next focus areas.
5. Aim for at most ~4,000 tokens of output total. Drop noise — minor one-off mistakes \
do not belong here.

Tone of ``prose_summary``: terse, professional, third-person — written for the coach to \
re-read, not for the learner. No sugar-coating, no praise inflation.

Output MUST conform exactly to the JSON schema. Do not output text outside the JSON.
"""


SUMMARIZATION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "prose_summary": {"type": "string"},
        "persistent_weaknesses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "evidence_count": {"type": "integer", "minimum": 1},
                    "last_seen": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["topic", "evidence_count", "last_seen", "severity"],
            },
        },
        "recurring_strengths": {"type": "array", "items": {"type": "string"}},
        "mastered_vocab_count": {"type": "integer", "minimum": 0},
        "current_level": {"type": "integer", "minimum": 1, "maximum": 10},
        "last_milestones": {"type": "array", "items": {"type": "string"}},
        "recommended_focus": {
            "type": "array",
            "minItems": 1,
            "maxItems": 4,
            "items": {"type": "string"},
        },
    },
    "required": [
        "prose_summary",
        "persistent_weaknesses",
        "recurring_strengths",
        "mastered_vocab_count",
        "current_level",
        "last_milestones",
        "recommended_focus",
    ],
}

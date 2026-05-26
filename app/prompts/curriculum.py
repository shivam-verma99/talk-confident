"""Curriculum-generation prompt + Gemini response schema."""

from __future__ import annotations

from typing import Any


CURRICULUM_INSTRUCTION = """\
Generate today's spoken-English practice plan tailored for this user.

Requirements:
- Pick 5 to 8 vocabulary words drawn from the user's persistent weaknesses and their \
daily professional needs (status updates, escalations, vendor calls, root-cause \
briefings, team deployments, maintenance reports). Avoid trivial or childish \
vocabulary. Avoid words the user has clearly mastered (mastery_score >= 0.85 in the \
supplied profile).
- For each word: definition, one short example USED IN A BSNL CONTEXT, and a one-line \
``why_chosen`` rationale tying it to a weakness or professional need.
- Compose 6 to 10 practice sentences using the selected words in realistic BSNL \
maintenance contexts (fiber cut, downtime, OFC restoration, vendor escalation, team \
deployment, status review). Vary sentence length and complexity within the user's \
current level. Each sentence should specify ``target_words`` and a 1-10 ``difficulty``.
- Never assume gender. Do not use "sir", "madam", or gendered pronouns. Refer to the \
user as "you" and to third parties by their role.
- ``focus_areas`` is a short list (max 4) of the gaps these sentences exercise.
- ``meeting_scenario_seed`` is OPTIONAL. Include it only if today is a good day for \
spontaneous practice (e.g., variety hasn't been seen in several days, or the user \
specifically requested meeting mode). If included, keep it short — title, context, \
role, opening_line.
- Do not output text outside the JSON schema.

Inputs you will receive:
- The user's current level and rolling weaknesses.
- The user's compressed history (in the persona system instruction).
- The mode requested by the caller (``read_aloud`` or ``meeting_prep``).
"""


CURRICULUM_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "level": {"type": "integer", "minimum": 1, "maximum": 10},
        "focus_areas": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 4,
        },
        "words": {
            "type": "array",
            "minItems": 5,
            "maxItems": 8,
            "items": {
                "type": "object",
                "properties": {
                    "word": {"type": "string"},
                    "definition": {"type": "string"},
                    "example": {"type": "string"},
                    "why_chosen": {"type": "string"},
                },
                "required": ["word", "definition", "example", "why_chosen"],
            },
        },
        "sentences": {
            "type": "array",
            "minItems": 6,
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "target_words": {"type": "array", "items": {"type": "string"}},
                    "difficulty": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["text", "target_words", "difficulty"],
            },
        },
        "meeting_scenario_seed": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "context": {"type": "string"},
                "role": {"type": "string"},
                "opening_line": {"type": "string"},
            },
            "required": ["title", "context", "role", "opening_line"],
        },
    },
    "required": ["level", "focus_areas", "words", "sentences"],
}


MEETING_SCENARIO_INSTRUCTION = """\
Generate a realistic BSNL maintenance / operations meeting scenario for spontaneous \
spoken-English practice. Tailor the scenario to the user's seniority (Divisional Engineer) \
and current weaknesses. Pick from themes such as fiber-cut restoration, downtime root \
cause analysis, vendor escalation, OFC route survey, staff deployment, customer SLA review.

Output a single scenario, fully self-contained, with: a short title; concrete context \
(2-4 sentences with specifics — exchange names, routes, durations, vendors are welcome); \
the user's role; the counterpart's role; 3-5 objectives the user must achieve in the \
conversation; an opening prompt the counterpart will say first to start the role-play; \
and 3-6 short ``suggested_phrases`` they can lean on when they get stuck.

Do not assume gender — no "sir", "madam", or gendered pronouns. Refer to the user as \
"you" or by role; refer to the counterpart by their professional role.

Do not emit text outside the JSON schema.
"""


MEETING_SCENARIO_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "context": {"type": "string"},
        "your_role": {"type": "string"},
        "counterpart_role": {"type": "string"},
        "objectives": {
            "type": "array",
            "minItems": 3,
            "maxItems": 5,
            "items": {"type": "string"},
        },
        "opening_prompt": {"type": "string"},
        "suggested_phrases": {
            "type": "array",
            "minItems": 3,
            "maxItems": 6,
            "items": {"type": "string"},
        },
    },
    "required": [
        "title",
        "context",
        "your_role",
        "counterpart_role",
        "objectives",
        "opening_prompt",
        "suggested_phrases",
    ],
}

"""Prompts + Gemini response schemas for the curated word-list feature.

Three distinct Gemini calls power this feature:

1. ``WORDLIST_ANALYSIS_INSTRUCTION`` — examines a fresh practice attempt and decides
   whether new words should be added to the user's active list. The model is asked to
   reason in terms of *patterns* (phoneme families, lexical gaps, grammar structures),
   not isolated mistakes, so the suggestions generalise.

2. ``SENTENCE_GENERATION_INSTRUCTION`` — given a selected slice of the user's active
   list plus a small "reinforcement set" of recently mastered words, returns a batch
   of practice sentences calibrated to the user's CEFR-style level.

3. ``WORD_DEFINITION_INSTRUCTION`` — when the user adds a bare word to their own list
   without a definition, the model fills in a clean definition + example.

All three return strict JSON conforming to the attached schema.
"""

from __future__ import annotations

from typing import Any


# =============================================================================
# 1. Word list analysis — derive new coach words from a fresh attempt
# =============================================================================

WORDLIST_ANALYSIS_INSTRUCTION = """\
You are reviewing one practice attempt to decide whether the user's curated
"active word list" needs new entries. Work like a phonetics-aware speech coach.

# Core principle — recommend patterns, not instances
For each weakness you see, identify the *underlying family* of error:

* Phoneme substitution / approximation (e.g., /θ/ → /d/, /v/ → /w/, /æ/ → /ɛ/,
  consonant cluster simplification, schwa avoidance in unstressed syllables).
* Suprasegmental gaps (lexical stress on wrong syllable, sentence-level rhythm,
  intonation pattern in question vs. statement).
* Lexical gaps (the user reached for a word they don't own — circumlocution,
  filler, native-language borrowing).
* Grammatical structures (article elision, subject-verb agreement, tense
  consistency, prepositions of time/place/manner, modal verb mis-selection).

Each recommended word must directly target one of these patterns. A word that
contains the problematic phoneme in a *trainable* position (clearly audible,
not buried in a cluster) is preferred over a "perfect" example the user cannot
hear themselves mispronounce.

# Constraints
* Recommend 0 to 3 words. Zero is a valid output — only suggest when there is
  *clear evidence* in this attempt that the gap is recurring or significant.
* Never recommend a word that appears in ``active_words`` or ``archived_words``
  in the input. Re-suggesting denied / mastered words is forbidden.
* Each word must come with: definition (under 18 words), a short, neutral example
  (one sentence, professional register, no pet names, no gendered address),
  ``target_weakness`` (5-12 words naming the *pattern* — e.g., "voiced th-fricative
  /ð/ at word onset"), and ``why_chosen`` (one sentence the user will read in
  their list explaining the link to their attempt).
* ``priority`` is 1-100. Default 50. Push to 70+ only if the pattern appeared
  ≥3 times in this attempt or in the recent_weaknesses history.
* Tone is neutral, professional, factual. No emoji, no exclamation marks, no
  praise inflation, no gendered address ("sir"/"madam"/"buddy"). Refer to the
  user by their pronouns if supplied, otherwise use second person ("you").

# Inputs
You will receive a JSON block containing:
- ``attempt``: { target_text, transcript, scores, weaknesses, corrections,
  hesitation_markers, overall_note }
- ``recent_weaknesses``: aggregated weakness strings from the last 10 attempts
- ``active_words``: list of words already in the user's active list
- ``archived_words``: list of words already archived (any reason)
- ``current_level``: 1-10
- ``user_pronouns``: the user's preferred pronoun phrase, or null

Return strict JSON matching the schema. Empty ``recommended_words`` is valid.
"""

WORDLIST_ANALYSIS_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommended_words": {
            "type": "array",
            "minItems": 0,
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "word": {"type": "string"},
                    "definition": {"type": "string"},
                    "example": {"type": "string"},
                    "target_weakness": {"type": "string"},
                    "why_chosen": {"type": "string"},
                    "priority": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                    },
                },
                "required": [
                    "word",
                    "definition",
                    "example",
                    "target_weakness",
                    "why_chosen",
                    "priority",
                ],
            },
        },
        "analysis_note": {"type": "string"},
    },
    "required": ["recommended_words"],
}


# =============================================================================
# 2. Sentence generation — given a selected slice of active words
# =============================================================================

SENTENCE_GENERATION_INSTRUCTION = """\
Generate practice sentences that exercise specific target words for this user.

# Mix ratio (mandatory)
* ~70% of the sentences must feature one or more ``focus_words`` (the active
  words the user explicitly chose to practice now). Lower-mastery focus words
  appear MORE often — they need the reps.
* ~30% of the sentences must feature at least one ``reinforcement_word`` (recently
  mastered words from the user's archived list). These sentences should be
  noticeably harder for the level — longer clauses, embedded subordinates,
  unfamiliar collocations — so retention is genuinely tested.
* Every sentence's ``target_words`` field MUST list exactly the words from the
  active/reinforcement input that appear in the sentence. Do not invent new ones.

# Difficulty calibration (mandatory)
The user is at ``current_level`` on a 1-10 scale (≈ CEFR A1 → C2):

* Levels 1-3: short concrete sentences (6-10 words), present tense, common
  workplace nouns, no idioms.
* Levels 4-6: 10-16 words, mix of tenses, professional context (status updates,
  briefings, vendor calls, escalations, planning), occasional subordination.
* Levels 7-10: 14-22 words, nuanced register, idiomatic expressions, embedded
  clauses, hedging language, modal stacking, conditional structures.

# Style
* Neutral, professional, gender-free. Refer to the speaker as "I" or "you".
  Refer to third parties by their role ("the engineer", "the vendor", "the
  client") not by gendered pronouns unless explicitly justified by the role.
* No sentences glorifying urgency or panic. No gamified or motivational copy.
* Each sentence must be plausible spoken English — readable aloud cleanly.
* Vary the syntactic structure across the batch so the user doesn't drill a
  single template five times.

# Output
Return ``count`` sentences (caller supplies, typically 5-8). Each sentence has:
text, target_words[], difficulty (1-10), and one-line rationale ``why_useful``.
Do not output anything outside the JSON schema.
"""

SENTENCE_GENERATION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sentences": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "target_words": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "difficulty": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                    },
                    "why_useful": {"type": "string"},
                },
                "required": ["text", "target_words", "difficulty", "why_useful"],
            },
        },
    },
    "required": ["sentences"],
}


# =============================================================================
# 3. Word definition — for user-added bare words
# =============================================================================

WORD_DEFINITION_INSTRUCTION = """\
The user added a single word to their personal practice list without a
definition. Return a clean, concise definition and one short example.

Rules:
* Definition: under 18 words, plain English, no circular ("X is X-ing")
  phrasing.
* Example: one sentence, professional register, gender-free, present-tense
  unless the word demands otherwise. 8-16 words.
* If the word has multiple senses, pick the one most useful in a workplace
  spoken-English context.
* If the word is genuinely not a word, return an empty definition string and
  set ``valid`` to false.

Return strict JSON.
"""

WORD_DEFINITION_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "valid": {"type": "boolean"},
        "definition": {"type": "string"},
        "example": {"type": "string"},
    },
    "required": ["valid", "definition", "example"],
}

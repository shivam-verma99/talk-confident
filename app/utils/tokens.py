"""Token-counting helpers.

We use Gemini's own ``count_tokens`` for authoritative counts but fall back to a
cheap heuristic when the network is unavailable (e.g. in unit tests).
"""

from __future__ import annotations

import asyncio
import logging
import math

from google import genai

logger = logging.getLogger(__name__)


def _heuristic_token_count(text: str) -> int:
    """~4 characters per token is the rule of thumb published by OpenAI/Google."""
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


async def count_tokens(client: genai.Client, model: str, text: str) -> int:
    """Best-effort token count for a single text string."""
    if not text:
        return 0
    try:
        result = await asyncio.to_thread(client.models.count_tokens, model=model, contents=text)
        return int(getattr(result, "total_tokens", 0)) or _heuristic_token_count(text)
    except Exception as exc:  # pragma: no cover - network failures
        logger.warning("count_tokens fell back to heuristic: %s", exc)
        return _heuristic_token_count(text)

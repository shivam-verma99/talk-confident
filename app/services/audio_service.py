"""Audio packaging for Gemini — fully in-memory, never touches disk.

Decision rule:
    < ``inline_audio_threshold_bytes``  → ``types.Part.from_bytes`` (InlineData).
    otherwise                           → upload via the Files API, wait for ACTIVE,
                                          return a URI part. Caller must delete the file
                                          once Gemini has finished consuming it.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from dataclasses import dataclass

from google import genai
from google.genai import types

from app.config import get_settings

logger = logging.getLogger(__name__)


ALLOWED_AUDIO_MIMES: frozenset[str] = frozenset(
    {
        "audio/wav",
        "audio/x-wav",
        "audio/mpeg",
        "audio/mp3",
        "audio/mp4",
        "audio/aac",
        "audio/ogg",
        "audio/webm",
        "audio/flac",
    }
)


# --------------------------------------------------------------------------- errors


class AudioValidationError(ValueError):
    """The uploaded audio failed our preflight checks."""


class AudioPreparationError(RuntimeError):
    """Something failed while shipping audio to Gemini."""


# --------------------------------------------------------------------------- model


@dataclass
class PreparedAudio:
    """Result of preparing a buffer for Gemini consumption."""

    part: types.Part
    gemini_file_name: str | None  # set only when we used the Files API
    size_bytes: int
    mime_type: str


# --------------------------------------------------------------------------- public API


def validate_audio_upload(*, mime_type: str | None, size_bytes: int) -> str:
    """Reject obviously bad uploads before we touch Gemini."""
    settings = get_settings()
    if not mime_type:
        raise AudioValidationError("Missing audio content-type.")
    mime = mime_type.lower().split(";", 1)[0].strip()
    if mime not in ALLOWED_AUDIO_MIMES:
        raise AudioValidationError(f"Unsupported audio mime type: {mime}")
    if size_bytes <= 0:
        raise AudioValidationError("Audio payload is empty.")
    if size_bytes > settings.max_audio_bytes:
        raise AudioValidationError(
            f"Audio payload {size_bytes} bytes exceeds limit of {settings.max_audio_bytes}."
        )
    return mime


async def prepare_audio_part(
    *,
    client: genai.Client,
    buffer: bytes,
    mime_type: str,
) -> PreparedAudio:
    """Package ``buffer`` for a Gemini ``generate_content`` call."""
    settings = get_settings()
    size = len(buffer)

    if size < settings.inline_audio_threshold_bytes:
        part = types.Part.from_bytes(data=buffer, mime_type=mime_type)
        return PreparedAudio(part=part, gemini_file_name=None, size_bytes=size, mime_type=mime_type)

    # Files API path: upload, wait for ACTIVE, return URI part.
    try:
        uploaded = await asyncio.to_thread(
            client.files.upload,
            file=io.BytesIO(buffer),
            config=types.UploadFileConfig(mime_type=mime_type),
        )
    except Exception as exc:
        raise AudioPreparationError(f"Files API upload failed: {exc}") from exc

    await _wait_until_active(client, uploaded.name)

    part = types.Part.from_uri(file_uri=uploaded.uri, mime_type=mime_type)
    return PreparedAudio(
        part=part,
        gemini_file_name=uploaded.name,
        size_bytes=size,
        mime_type=mime_type,
    )


async def delete_gemini_file(client: genai.Client, name: str | None) -> None:
    """Best-effort cleanup — safe to call with ``None``."""
    if not name:
        return
    try:
        await asyncio.to_thread(client.files.delete, name=name)
    except Exception as exc:  # pragma: no cover - cleanup is best-effort
        logger.warning("Failed to delete Gemini file %s: %s", name, exc)


# --------------------------------------------------------------------------- helpers


async def _wait_until_active(
    client: genai.Client,
    name: str,
    *,
    timeout_s: float = 30.0,
    poll_interval_s: float = 0.5,
) -> None:
    """Poll the Files API until the uploaded file becomes ACTIVE."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            file = await asyncio.to_thread(client.files.get, name=name)
        except Exception as exc:
            raise AudioPreparationError(f"Files API get failed: {exc}") from exc

        state = getattr(file, "state", None)
        state_name = getattr(state, "name", str(state) if state is not None else "")
        if state_name == "ACTIVE":
            return
        if state_name == "FAILED":
            raise AudioPreparationError(f"Gemini reported file FAILED: {name}")

        if time.monotonic() > deadline:
            raise AudioPreparationError(
                f"Gemini file did not reach ACTIVE within {timeout_s:.0f}s (state={state_name})."
            )
        await asyncio.sleep(poll_interval_s)

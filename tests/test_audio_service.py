"""Unit tests for audio_service."""

from __future__ import annotations

import pytest

from app.services.audio_service import (
    AudioValidationError,
    prepare_audio_part,
    validate_audio_upload,
)


def test_validate_rejects_empty():
    with pytest.raises(AudioValidationError):
        validate_audio_upload(mime_type="audio/wav", size_bytes=0)


def test_validate_rejects_unknown_mime():
    with pytest.raises(AudioValidationError):
        validate_audio_upload(mime_type="video/mp4", size_bytes=1024)


def test_validate_accepts_wav():
    assert validate_audio_upload(mime_type="audio/wav", size_bytes=1024) == "audio/wav"


def test_validate_strips_parameters():
    assert (
        validate_audio_upload(mime_type="audio/mpeg; codecs=mp3", size_bytes=1024)
        == "audio/mpeg"
    )


@pytest.mark.asyncio
async def test_prepare_inline_path(fake_genai):
    buf = b"\x00" * 1024  # small → inline
    prepared = await prepare_audio_part(client=fake_genai, buffer=buf, mime_type="audio/wav")
    assert prepared.gemini_file_name is None  # no Files API used
    assert prepared.size_bytes == len(buf)


@pytest.mark.asyncio
async def test_prepare_files_api_path(monkeypatch, fake_genai):
    # Force threshold low to drive the Files API branch.
    from app import config

    monkeypatch.setattr(
        config.get_settings(), "inline_audio_threshold_bytes", 512, raising=False
    )
    buf = b"\x00" * 2048
    prepared = await prepare_audio_part(client=fake_genai, buffer=buf, mime_type="audio/wav")
    assert prepared.gemini_file_name == "files/test"

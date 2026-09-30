"""Tests for a sealed recording: sealed a chunk at a time, and any range opened on its own."""

import os
from pathlib import Path

import pytest

from pinecall.domain.errors import UpstreamFailed
from pinecall.process.sealed_audio import (
    CHUNK,
    HEADER,
    MAGIC,
    SEALED_CHUNK,
    Span,
    new_key,
    on_disk,
    opened,
    recorded_size,
    seal_file,
    span_of,
)

# Three chunks and a half: a range can start in one and end in another, and the last is short.
AUDIO = b"OggS" + os.urandom(3 * CHUNK + CHUNK // 2)


def sealed_beside(tmp_path: Path, audio: bytes, key: bytes) -> Path:
    plain = tmp_path / "audio.ogg"
    plain.write_bytes(audio)
    sealed = tmp_path / "audio.sealed"
    seal_file(plain, sealed, key)
    return sealed


async def read_span(path: Path, key: bytes, span: Span) -> bytes:
    kept = await on_disk("CA_1", path)
    assert kept is not None
    return b"".join([piece async for piece in opened(kept, key, span)])


async def test_the_sealed_file_holds_nothing_of_the_audio_and_says_how_long_it_was(
    tmp_path: Path,
) -> None:
    sealed = sealed_beside(tmp_path, AUDIO, new_key())
    kept = sealed.read_bytes()
    assert kept.startswith(MAGIC)
    assert AUDIO[:64] not in kept
    assert not (tmp_path / "audio.ogg").exists()
    assert recorded_size(len(kept)) == len(AUDIO)


@pytest.mark.parametrize(
    ("first", "last"),
    [
        (0, 3),
        (0, len(AUDIO) - 1),
        (CHUNK - 2, CHUNK + 2),
        (2 * CHUNK, 3 * CHUNK + 10),
        (len(AUDIO) - 5, len(AUDIO) - 1),
    ],
)
async def test_any_range_opens_the_bytes_that_were_recorded_there(
    tmp_path: Path, first: int, last: int
) -> None:
    key = new_key()
    sealed = sealed_beside(tmp_path, AUDIO, key)
    assert await read_span(sealed, key, Span(first, last)) == AUDIO[first : last + 1]


async def test_an_empty_recording_and_one_of_whole_chunks_open_too(tmp_path: Path) -> None:
    key = new_key()
    for audio in (b"", os.urandom(2 * CHUNK)):
        sealed = sealed_beside(tmp_path, audio, key)
        assert recorded_size(sealed.stat().st_size) == len(audio)
        if audio:
            assert await read_span(sealed, key, Span(0, len(audio) - 1)) == audio


async def test_a_file_cut_short_or_touched_or_under_another_key_does_not_open(
    tmp_path: Path,
) -> None:
    key = new_key()
    sealed = sealed_beside(tmp_path, AUDIO, key)
    whole = sealed.read_bytes()
    cut = whole[: len(whole) - (len(whole) - HEADER) % SEALED_CHUNK]
    touched = whole[:100] + bytes([whole[100] ^ 1]) + whole[101:]
    for kept, opening in ((cut, key), (touched, key), (whole, new_key())):
        sealed.write_bytes(kept)
        size = recorded_size(len(kept))
        with pytest.raises(UpstreamFailed, match="does not open"):
            await read_span(sealed, opening, Span(0, size - 1))


def test_a_players_range_is_read_as_the_http_header_says() -> None:
    assert span_of(None, 100) is None
    assert span_of("bytes=10-19", 100) == Span(10, 19)
    assert span_of("bytes=90-", 100) == Span(90, 99)
    assert span_of("bytes=-10", 100) == Span(90, 99)
    assert span_of("bytes=95-200", 100) == Span(95, 99)
    assert span_of("bytes=0-1,5-6", 100) is None
    assert span_of("bytes=20-10", 100) is None
    assert span_of("bytes=x-5", 100) is None
    assert span_of("bytes=120-", 100) == Span(120, 99)

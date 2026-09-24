"""Which file a call plays while a tool runs, as the worker resolves it before the session."""

from __future__ import annotations

from pathlib import Path

import pytest

from pinecall.session.hold_audio import DEFAULT
from pinecall.worker.client import HoldAudioSaid
from pinecall.worker.hold import the_melody
from pinecall.worker.hop import GatewayRefused

pytestmark = pytest.mark.unit

A_HASH = "a" * 64


class Gateway:
    """The two hold doors of the gateway, answering what the test says, counting the downloads."""

    def __init__(self, said: HoldAudioSaid | None, audio: bytes = b"OggS clip") -> None:
        self._said = said
        self._audio = audio
        self.downloads = 0

    async def hold_audio(self, _slug: str, **_: object) -> HoldAudioSaid:
        if self._said is None:
            raise GatewayRefused("GET /v1/agents/x/hold-audio: 503")
        return self._said

    async def hold_audio_file(self, _slug: str, **_: object) -> bytes:
        self.downloads += 1
        return self._audio


async def resolved(gateway: Gateway, cache: Path) -> Path | None:
    return await the_melody(gateway, "clinica-norte", org=None, env=None, holder=None, cache=cache)  # pyright: ignore[reportArgumentType] — the two doors are all it asks


async def test_the_default_is_the_file_the_runtime_ships(tmp_path: Path) -> None:
    assert await resolved(Gateway(HoldAudioSaid(played="default")), tmp_path) == DEFAULT


async def test_off_is_no_file_at_all(tmp_path: Path) -> None:
    assert await resolved(Gateway(HoldAudioSaid(played="off")), tmp_path) is None


async def test_a_clip_is_downloaded_once_and_kept_by_its_hash(tmp_path: Path) -> None:
    gateway = Gateway(HoldAudioSaid(played="custom", sha256=A_HASH))
    first = await resolved(gateway, tmp_path)
    again = await resolved(gateway, tmp_path)
    assert first == again == tmp_path / f"{A_HASH}.ogg"
    assert (tmp_path / f"{A_HASH}.ogg").read_bytes() == b"OggS clip"
    assert gateway.downloads == 1


async def test_a_gateway_that_cannot_say_plays_the_default_rather_than_nothing(
    tmp_path: Path,
) -> None:
    assert await resolved(Gateway(None), tmp_path) == DEFAULT

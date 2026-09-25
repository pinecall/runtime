"""A sample: the vendor's frames joined into one WAV, timed, and a vendor's refusal named."""

from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from livekit import rtc
from livekit.agents import APIStatusError

from pinecall._settings import Settings
from pinecall.providers.registry import Asked
from pinecall.providers.tts import sampling
from pinecall.providers.tts.sampling import SampleRefused, a_sample

pytestmark = pytest.mark.unit

RATE = 24_000


@dataclass
class Said:
    frame: rtc.AudioFrame


def a_frame(samples: int) -> rtc.AudioFrame:
    return rtc.AudioFrame(b"\x01\x00" * samples, RATE, 1, samples)


class AVoice:
    """A vendor that says two frames, or refuses the way a plugin does."""

    def __init__(self, refuses: bool = False) -> None:
        self.refuses = refuses
        self.closed = False

    def synthesize(self, text: str) -> AsyncIterator[Said]:
        del text
        return self._frames()

    async def _frames(self) -> AsyncIterator[Said]:
        if self.refuses:
            raise APIStatusError("Not Found", status_code=404)
        yield Said(a_frame(240))
        yield Said(a_frame(480))

    async def aclose(self) -> None:
        self.closed = True


def spoken_by(voice: AVoice, monkeypatch: pytest.MonkeyPatch) -> None:
    def build(vendor: str, asked: Asked) -> AVoice:
        del vendor, asked
        return voice

    monkeypatch.setattr(sampling.VENDORS, "build", build)


async def test_the_frames_become_one_wav_at_the_vendors_rate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voice = AVoice()
    spoken_by(voice, monkeypatch)

    heard = await a_sample("cartesia", Asked(settings=Settings()), "Hola")

    with wave.open(io.BytesIO(heard.wav)) as file:
        assert (file.getframerate(), file.getnchannels(), file.getnframes()) == (RATE, 1, 720)
    assert 0 <= heard.first_audio_ms <= heard.total_ms
    assert voice.closed, "the plugin's sockets are let go"


async def test_a_vendor_that_says_no_is_named_in_the_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voice = AVoice(refuses=True)
    spoken_by(voice, monkeypatch)

    with pytest.raises(SampleRefused, match="cartesia did not say it: Not Found"):
        await a_sample("cartesia", Asked(settings=Settings()), "Hola")
    assert voice.closed

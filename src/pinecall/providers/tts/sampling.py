"""One sentence said in a voice, outside any call, timed: what a person hears before choosing it."""

from __future__ import annotations

import io
import time
import wave
from dataclasses import dataclass

from livekit.agents import APIError
from livekit.agents.tts import ChunkedStream
from livekit.agents.utils import http_context

from pinecall.providers.registry import Asked
from pinecall.providers.tts import VENDORS

SAMPLE_WIDTH = 2  # 16-bit PCM, which is what every plugin's frames carry

REFUSED = "{vendor} did not say it: {why}"


class SampleRefused(Exception):
    """The vendor was asked and said no: a voice it does not have, a model, a key it refused."""


@dataclass(frozen=True)
class Sample:
    """The sentence as a WAV file, and the two numbers a caller would feel of it."""

    wav: bytes
    # From asking to the first audio: the wait a caller hears after they stop talking, the part of
    # latency the voice owns. A vendor is chosen on this number as much as on how it sounds.
    first_audio_ms: int
    total_ms: int


# The same vendor file a call builds its voice with, so a sample is exactly what a caller would
# hear — the model, the language and the voice go through the plugin the same way. The plugin
# reaches the vendor through livekit's http session, which only a job binds, so the door opens one
# of its own for as long as the sentence takes (utils/http_context.py, as evals/calling.py does).
async def a_sample(vendor: str, asked: Asked, text: str) -> Sample:
    """The text said by that vendor as asked, as a WAV, with how long it took to start and end."""
    async with http_context.open():
        speech = VENDORS.build(vendor, asked)
        try:
            return await _said(speech.synthesize(text))
        except APIError as refused:
            raise SampleRefused(REFUSED.format(vendor=vendor, why=refused.message)) from refused
        finally:
            await speech.aclose()


async def _said(stream: ChunkedStream) -> Sample:
    started = time.perf_counter()
    first: float | None = None
    pcm = bytearray()
    rate = 0
    async for audio in stream:
        if first is None:
            first = time.perf_counter()
        pcm.extend(bytes(audio.frame.data))
        rate = audio.frame.sample_rate
    ended = time.perf_counter()
    return Sample(
        wav=_a_wav(bytes(pcm), rate),
        first_audio_ms=_ms(started, first if first is not None else ended),
        total_ms=_ms(started, ended),
    )


def _a_wav(pcm: bytes, rate: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(SAMPLE_WIDTH)
        file.setframerate(rate or 24_000)
        file.writeframes(pcm)
    return out.getvalue()


def _ms(started: float, at: float) -> int:
    return round((at - started) * 1000)

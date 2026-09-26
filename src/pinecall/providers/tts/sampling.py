"""One sentence said in a voice, outside any call, timed: what a person hears before choosing it."""

from __future__ import annotations

import io
import time
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass

from livekit.agents import APIError, APIStatusError
from livekit.agents.tts import SynthesizedAudio
from livekit.agents.utils import http_context

from pinecall.providers.language import primary
from pinecall.providers.registry import Asked
from pinecall.providers.tts import VENDORS

SAMPLE_WIDTH = 2  # 16-bit PCM, which is what every plugin's frames carry

# What a voice says when the person gave it no words: one line in the language, so a Spanish
# voice is not judged reading English. The gateway keeps this table so that every client — the
# console's picker, `pinecall voices play` — hears the same line and none carries a copy.
A_LINE_FOR: dict[str, str] = {
    "es": "Hola, gracias por llamar. ¿En qué le puedo ayudar?",
    "en": "Hi, thanks for calling. How can I help you today?",
}

REFUSED = "{vendor} did not say it: {why}"
NOTHING_SAID = "{vendor} answered with no audio at all"


class SampleRefused(Exception):
    """The vendor was asked and said no — a voice it does not have, a model, a key it refused —
    with the vendor's own status when it gave one."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Sample:
    """The sentence as a WAV file, and the two numbers a caller would feel of it."""

    wav: bytes
    # From the words to the first audio, over the same websocket a call speaks on, its connection
    # included: the wait a caller hears after they stop talking, the part of latency the voice
    # owns. A call's worker keeps that connection warm between turns, so a call's own number sits
    # a little under this one; between two voices at one vendor, the comparison holds.
    first_audio_ms: int
    total_ms: int


def a_line_for(language: str | None) -> str:
    """The line a voice reads when nobody wrote one: its language's, else the English one."""
    return A_LINE_FOR.get(primary(language) or "", A_LINE_FOR["en"])


# The same vendor file a call builds its voice with, so a sample is what a caller would hear —
# the model, the language and the voice go through the plugin the same way, and over the same
# streaming path a call speaks on where the plugin has one. The plugin reaches the vendor through
# livekit's http session, which only a job binds, so the door opens one of its own for as long as
# the sentence takes (utils/http_context.py, as evals/voice_run.py does).
async def a_sample(vendor: str, asked: Asked, text: str) -> Sample:
    """The text said by that vendor as asked, as a WAV, with how long it took to start and end."""
    async with http_context.open():
        speech = VENDORS.build(vendor, asked)
        try:
            if speech.capabilities.streaming:
                async with speech.stream() as stream:
                    stream.push_text(text)
                    stream.end_input()
                    heard = await _said(vendor, stream)
            else:
                async with speech.synthesize(text) as chunks:
                    heard = await _said(vendor, chunks)
        except APIStatusError as refused:
            raise SampleRefused(
                REFUSED.format(vendor=vendor, why=refused.message), refused.status_code
            ) from refused
        except APIError as broke:
            raise SampleRefused(REFUSED.format(vendor=vendor, why=broke.message)) from broke
        finally:
            await speech.aclose()
        return heard


async def _said(vendor: str, audio: AsyncIterator[SynthesizedAudio]) -> Sample:
    started = time.perf_counter()
    first: float | None = None
    pcm = bytearray()
    rate, channels = 0, 0
    async for chunk in audio:
        if first is None:
            first = time.perf_counter()
        pcm.extend(bytes(chunk.frame.data))
        rate, channels = chunk.frame.sample_rate, chunk.frame.num_channels
    ended = time.perf_counter()
    if first is None or not pcm:
        raise SampleRefused(NOTHING_SAID.format(vendor=vendor))
    return Sample(
        wav=_a_wav(bytes(pcm), rate, channels),
        first_audio_ms=_ms(started, first),
        total_ms=_ms(started, ended),
    )


def _a_wav(pcm: bytes, rate: int, channels: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(channels)
        file.setsampwidth(SAMPLE_WIDTH)
        file.setframerate(rate)
        file.writeframes(pcm)
    return out.getvalue()


def _ms(started: float, at: float) -> int:
    return round((at - started) * 1000)

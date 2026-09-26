"""A sample: the vendor's frames joined into one WAV, timed, and a vendor's refusal named."""

from __future__ import annotations

import io
import wave
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace, TracebackType

import pytest
from livekit import rtc
from livekit.agents import APIConnectionError, APIStatusError

from pinecall.providers.registry import Asked
from pinecall.providers.tts import sampling
from pinecall.providers.tts.sampling import A_LINE_FOR, SampleRefused, sample_line_for, speak_sample
from pinecall.settings import Settings

pytestmark = pytest.mark.unit

RATE = 24_000


@dataclass
class Said:
    frame: rtc.AudioFrame


def a_frame(samples: int) -> rtc.AudioFrame:
    return rtc.AudioFrame(b"\x01\x00" * samples, RATE, 1, samples)


class AStream:
    """What a plugin's stream or chunked stream is to the sampler: a context that yields frames."""

    def __init__(self, voice: AVoice) -> None:
        self.voice = voice

    async def __aenter__(self) -> AStream:
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.voice.streams_closed += 1

    def push_text(self, text: str) -> None:
        self.voice.pushed.append(text)

    def end_input(self) -> None:
        self.voice.ended = True

    async def __aiter__(self) -> AsyncIterator[Said]:
        if self.voice.refuses is not None:
            raise self.voice.refuses
        for frame in self.voice.frames:
            yield Said(frame)


class AVoice:
    """A vendor that says two frames, over a websocket or not, or refuses the way a plugin does."""

    def __init__(
        self,
        *,
        streaming: bool = True,
        refuses: Exception | None = None,
        frames: list[rtc.AudioFrame] | None = None,
    ) -> None:
        self.capabilities = SimpleNamespace(streaming=streaming)
        self.refuses = refuses
        self.frames = [a_frame(240), a_frame(480)] if frames is None else frames
        self.pushed: list[str] = []
        self.ended = False
        self.synthesized: list[str] = []
        self.streams_closed = 0
        self.closed = False

    def stream(self) -> AStream:
        return AStream(self)

    def synthesize(self, text: str) -> AStream:
        self.synthesized.append(text)
        return AStream(self)

    async def aclose(self) -> None:
        self.closed = True


def spoken_by(voice: AVoice, monkeypatch: pytest.MonkeyPatch) -> None:
    def build(vendor: str, asked: Asked) -> AVoice:
        del vendor, asked
        return voice

    monkeypatch.setattr(sampling.VENDORS, "build", build)


async def test_a_streaming_vendor_is_asked_over_its_stream_as_a_call_speaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voice = AVoice()
    spoken_by(voice, monkeypatch)

    heard = await speak_sample("cartesia", Asked(settings=Settings(world="production")), "Hola")

    assert (voice.pushed, voice.ended, voice.synthesized) == (["Hola"], True, [])
    with wave.open(io.BytesIO(heard.wav)) as file:
        assert (file.getframerate(), file.getnchannels(), file.getnframes()) == (RATE, 1, 720)
    assert 0 <= heard.first_audio_ms <= heard.total_ms
    assert voice.streams_closed == 1 and voice.closed, "the stream and the plugin are let go"


async def test_a_vendor_with_no_stream_is_asked_for_the_sentence_whole(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voice = AVoice(streaming=False)
    spoken_by(voice, monkeypatch)

    await speak_sample("deepgram", Asked(settings=Settings(world="production")), "Hi")

    assert (voice.synthesized, voice.pushed) == (["Hi"], [])


async def test_a_vendor_that_says_no_is_named_with_its_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voice = AVoice(refuses=APIStatusError("Not Found", status_code=404))
    spoken_by(voice, monkeypatch)

    with pytest.raises(SampleRefused, match="cartesia did not say it: Not Found") as raised:
        await speak_sample("cartesia", Asked(settings=Settings(world="production")), "Hola")
    assert raised.value.status == 404 and voice.closed


async def test_a_vendor_that_does_not_answer_is_named_with_no_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spoken_by(AVoice(refuses=APIConnectionError("no route")), monkeypatch)

    with pytest.raises(SampleRefused, match="no route") as raised:
        await speak_sample("cartesia", Asked(settings=Settings(world="production")), "Hola")
    assert raised.value.status is None


async def test_a_vendor_that_answers_with_no_audio_is_a_refusal_and_not_a_mute_wav(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spoken_by(AVoice(frames=[]), monkeypatch)

    with pytest.raises(SampleRefused, match="no audio at all"):
        await speak_sample("cartesia", Asked(settings=Settings(world="production")), "Hola")


def test_the_line_a_voice_reads_when_nobody_wrote_one_is_its_languages() -> None:
    assert sample_line_for("es-ES") == A_LINE_FOR["es"]
    assert sample_line_for("en") == A_LINE_FOR["en"]
    assert sample_line_for("fr") == A_LINE_FOR["en"]
    assert sample_line_for(None) == A_LINE_FOR["en"]

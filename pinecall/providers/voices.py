"""The voice picker: the voices a vendor lists, and a line spoken by one of them outside a call."""

import dataclasses
import inspect
import io
import time
import wave
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

from livekit.agents import APIError, APIStatusError
from livekit.agents.tts import SynthesizedAudio
from livekit.agents.utils import http_context

from pinecall.domain.errors import NotFound, UpstreamFailed
from pinecall.domain.types import Json, JsonObject
from pinecall.providers.build import Running, a_list, a_mapping, tts_of

SAMPLE_WIDTH = 2  # 16-bit PCM, as every plugin emits it

# Plugins name a voice's id and name differently; the first a row carries is it.
_THE_ID = ("id", "voice_id", "voice")
_THE_NAME = ("name", "voice_name", "display_name")

NOT_LISTED = "{vendor} lists no voices here: its voice is the vendor's own id, set as it is"


@dataclass(frozen=True)
class Listed:
    """A voice as the picker shows it: its id, its name, and the vendor's row whole."""

    id: str
    name: str
    detail: JsonObject


@dataclass(frozen=True)
class Sample:
    """A line spoken as WAV, with when its first audio came and when it ended."""

    wav: bytes
    # Includes setting up the connection, which a call already warmed.
    first_audio_ms: int
    total_ms: int


async def voices(running: Running) -> list[Listed]:
    """The voices a vendor lists on the stage's key, where its plugin lists any; NotFound if not."""
    async with http_context.open():
        speech = tts_of(running)
        listing = getattr(speech, "list_voices", None)
        if not callable(listing):
            raise NotFound(NOT_LISTED.format(vendor=running.vendor))
        try:
            asked = listing()
            if not inspect.isawaitable(asked):
                raise NotFound(NOT_LISTED.format(vendor=running.vendor))
            answered: object = await asked
        except APIError as refused:
            raise _refused(running.vendor, "did not list its voices", refused) from refused
        finally:
            await speech.aclose()
    return [voice for row in _rows(answered) if (voice := _listed(row)) is not None]


async def sample(running: Running, line: str) -> Sample:
    """The line spoken by the stage's voice, built as a call builds it, or the vendor's words."""
    async with http_context.open():
        speech = tts_of(running)
        try:
            if speech.capabilities.streaming:
                async with speech.stream() as stream:
                    stream.push_text(line)
                    stream.end_input()
                    return await _heard(stream)
            async with speech.synthesize(line) as chunks:
                return await _heard(chunks)
        except APIError as refused:
            raise _refused(running.vendor, "did not say it", refused) from refused
        finally:
            await speech.aclose()


# The vendor's own words, and its status where it answered with one.
def _refused(vendor: str, what: str, error: APIError) -> UpstreamFailed:
    status = f" ({error.status_code})" if isinstance(error, APIStatusError) else ""
    return UpstreamFailed(f"{vendor} {what}{status}: {error.message}")


# A listing is a list of rows, each a dataclass or a mapping; one that is a mapping of names
# is read as rows named by its keys.
def _rows(answered: object) -> list[JsonObject]:
    if a_mapping(answered):
        return [{"id": key, "name": key} for key in answered]
    return [_row(one) for one in answered] if a_list(answered) else []


def _row(one: object) -> JsonObject:
    if dataclasses.is_dataclass(one) and not isinstance(one, type):
        return _json(dataclasses.asdict(one))
    return _json(one) if a_mapping(one) else {}


def _json(fields: Mapping[str, object]) -> JsonObject:
    return {name: _value(value) for name, value in fields.items()}


def _value(value: object) -> Json:
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if a_mapping(value):
        return _json(value)
    if a_list(value):
        return [_value(one) for one in value]
    return str(value)


def _listed(row: JsonObject) -> Listed | None:
    named = next((str(row[name]) for name in _THE_ID if row.get(name)), None)
    if named is None:
        return None
    shown = next((str(row[name]) for name in _THE_NAME if row.get(name)), named)
    return Listed(id=named, name=shown, detail=row)


# livekit refuses a synthesis that pushed no audio, so what reaches here has some.
async def _heard(audio: AsyncIterator[SynthesizedAudio]) -> Sample:
    started = time.perf_counter()
    first: float | None = None
    pcm = bytearray()
    rate, channels = 0, 0
    async for chunk in audio:
        first = first or time.perf_counter()
        pcm += bytes(chunk.frame.data)
        rate, channels = chunk.frame.sample_rate, chunk.frame.num_channels
    ended = time.perf_counter()
    return Sample(
        wav=_wav(bytes(pcm), rate, channels),
        first_audio_ms=_ms(started, first or ended),
        total_ms=_ms(started, ended),
    )


def _wav(pcm: bytes, rate: int, channels: int) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(channels)
        file.setsampwidth(SAMPLE_WIDTH)
        file.setframerate(rate)
        file.writeframes(pcm)
    return out.getvalue()


def _ms(started: float, at: float) -> int:
    return round((at - started) * 1000)

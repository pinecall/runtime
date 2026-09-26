"""The hold melody's doors: the default every agent ships with, a file of yours, off, its bytes."""

from __future__ import annotations

import io
import math
from collections.abc import Iterator
from typing import Any, cast

import av
import httpx
import pytest

from pinecall.api.agents.hold_melody import MAX_BYTES, the_hold_audio
from pinecall.api.agents.registry import Registry
from pinecall.api.app import app
from pinecall.orgs.hold_melody import MemoryHoldAudio
from pinecall.orgs.tuning_store import MemoryTuning
from pinecall.session.hold_melody import DEFAULT, NOT_AUDIO
from pinecall.worker.client import Gateway
from tests.api.agents.pipeline.conftest import declared
from tests.api.conftest import AGENT, PIPELINE

pytestmark = pytest.mark.unit

HOLD = f"{PIPELINE}/hold-audio"


@pytest.fixture(autouse=True)
def kept() -> Iterator[MemoryHoldAudio]:
    """Nothing chosen at the start of every test: every agent plays the runtime's own melody."""
    chosen = MemoryHoldAudio()
    app.dependency_overrides[the_hold_audio] = lambda: chosen
    yield chosen
    app.dependency_overrides.pop(the_hold_audio, None)


def a_wav(seconds: float = 2.0, rate: int = 8000) -> bytes:
    """A sine tone as a wav file, the way a phone system exports one: 8 kHz, 16-bit, mono."""
    out = io.BytesIO()
    with cast(Any, av.open(out, "w", format="wav")) as sink:
        stream: Any = sink.add_stream("pcm_s16le", rate=rate)
        stream.layout = "mono"
        samples = int(seconds * rate)
        tone = b"".join(
            int(8000 * math.sin(2 * math.pi * 440 * at / rate)).to_bytes(2, "little", signed=True)
            for at in range(samples)
        )
        frame: Any = av.AudioFrame(format="s16", layout="mono", samples=samples)
        frame.planes[0].update(tone)
        frame.rate = rate
        for packet in stream.encode(frame):
            sink.mux(packet)
        for packet in stream.encode(None):
            sink.mux(packet)
    return out.getvalue()


async def test_an_agent_nobody_told_plays_the_melody_it_ships_with(
    fleet_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    await declared(registry, tuning)
    said = (await fleet_http.get(HOLD)).json()
    assert (said["played"], said["name"]) == ("default", "A New Life")
    heard = await fleet_http.get(f"{HOLD}/audio")
    assert heard.headers["content-type"] == "audio/ogg"
    assert heard.content == DEFAULT.read_bytes()


async def test_a_wav_of_yours_is_converted_and_is_what_the_next_call_plays(
    fleet_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning, worker_gateway: Gateway
) -> None:
    await declared(registry, tuning)
    put = await fleet_http.put(
        HOLD,
        content=a_wav(),
        params={"name": "espera.wav"},
        headers={"content-type": "audio/wav"},
    )
    assert put.status_code == 200, put.text
    said = put.json()
    assert (said["played"], said["name"], said["seconds"]) == ("custom", "espera.wav", 2.0)
    heard = (await fleet_http.get(f"{HOLD}/audio")).content
    with cast(Any, av.open(io.BytesIO(heard))) as clip:
        audio = clip.streams.audio[0]
        assert (audio.codec_context.name, audio.rate, audio.channels) == ("opus", 48000, 1)
    # The worker's own door says the same, by the hash it keeps the clip under.
    told = await worker_gateway.hold_audio(AGENT)
    assert (told.played, told.sha256) == ("custom", said["sha256"])
    assert await worker_gateway.hold_audio_file(AGENT) == heard


async def test_off_plays_nothing_and_default_brings_the_melody_back(
    fleet_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    await declared(registry, tuning)
    await fleet_http.put(HOLD, content=a_wav(), headers={"content-type": "audio/wav"})
    off = await fleet_http.put(f"{HOLD}/played", json={"played": "off"})
    assert off.json() == {"played": "off", "name": None, "seconds": None, "sha256": None}
    assert (await fleet_http.get(f"{HOLD}/audio")).status_code == 404
    back = await fleet_http.put(f"{HOLD}/played", json={"played": "default"})
    assert back.json()["played"] == "default"
    assert (await fleet_http.get(f"{HOLD}/audio")).content == DEFAULT.read_bytes()


async def test_what_is_no_audio_is_refused_in_a_sentence_and_nothing_changes(
    fleet_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    await declared(registry, tuning)
    refused = await fleet_http.put(HOLD, content=b"%PDF-1.7 not a melody")
    assert (refused.status_code, refused.json()["detail"]) == (400, NOT_AUDIO)
    assert (await fleet_http.get(HOLD)).json()["played"] == "default"


async def test_a_file_over_the_cap_is_refused_before_anything_decodes_it(
    fleet_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    await declared(registry, tuning)
    refused = await fleet_http.put(HOLD, content=b"\0" * (MAX_BYTES + 1))
    assert refused.status_code == 413


async def test_an_agent_no_app_holds_has_no_melody_to_choose(
    fleet_http: httpx.AsyncClient,
) -> None:
    assert (await fleet_http.get("/v1/agents/nobody/pipeline/hold-audio")).status_code == 404

"""Tests for an agent's pipeline: the report, and the melody a caller hears while a tool runs."""

import io
import math
import struct
import wave

from pinecall.tenancy import vault
from tests.conftest import AGENT, Knocking, postgres
from tests.gateway.api.conftest import an_app

PIPELINE = f"/v1/agents/{AGENT}/pipeline"
HOLD = f"{PIPELINE}/hold-audio"


def a_wav(seconds: float = 2.0) -> bytes:
    """A sine of that length, 16 kHz mono, as a browser would upload it."""
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(16_000)
        file.writeframes(
            b"".join(
                struct.pack("<h", int(8000 * math.sin(at / 10)))
                for at in range(int(16_000 * seconds))
            )
        )
    return out.getvalue()


@postgres
async def test_the_report_says_the_stages_the_catalogue_and_that_nothing_is_measured_yet(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as org:
        report = await org.get(PIPELINE)
    assert report.status_code == 200
    body = report.json()
    assert body["agent"] == AGENT
    assert (body["hears"]["vendor"], body["decides"]["vendor"], body["speaks"]["vendor"]) == (
        "acme",
        "acme",
        "acme",
    )
    assert body["defaults"] == {"llm": "acme", "stt": "acme", "tts": "acme"}
    assert [row["name"] for row in body["providers"] if row["name"] == "acme"] == ["acme"]
    assert (body["calls"], body["medians"], body["unavailable_reasons"]) == (0, [], {})
    await app.close()


@postgres
async def test_a_stage_with_no_key_to_run_on_is_named_with_its_reason(knocking: Knocking) -> None:
    await vault.drop_box_credentials(knocking.gateway.connections.pool, "acme")
    async with knocking.http(knocking.app["sandbox"]) as org:
        report = await org.get(PIPELINE)
    reasons = report.json()["unavailable_reasons"]
    assert set(reasons) == {"hears", "decides", "speaks"}
    assert "acme has no key" in reasons["decides"]


@postgres
async def test_an_upload_is_converted_once_and_played_until_silence_or_the_default_is_chosen(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        before = await org.get(HOLD)
        uploaded = await org.put(HOLD, content=a_wav(), params={"name": "jingle.wav"})
        clip = await org.get(f"{HOLD}/audio")
        off = await org.put(f"{HOLD}/played", json={"played": "off"})
        gone = await org.get(f"{HOLD}/audio")
        default = await org.put(f"{HOLD}/played", json={"played": "default"})
    assert before.json() == {"played": "default", "sha256": None, "seconds": None, "name": None}
    assert uploaded.status_code == 200
    assert (uploaded.json()["played"], uploaded.json()["name"]) == ("custom", "jingle.wav")
    assert uploaded.json()["seconds"] == 2.0
    assert clip.headers["content-type"] == "audio/ogg"
    assert clip.content[:4] == b"OggS"
    assert off.json()["played"] == "off"
    assert gone.status_code == 404
    assert default.json()["played"] == "default"


@postgres
async def test_a_file_that_is_no_melody_and_one_too_short_are_refused_in_words(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        noise = await org.put(HOLD, content=b"not audio at all")
        short = await org.put(HOLD, content=a_wav(0.2))
    assert noise.status_code == 400
    assert "no audio this box can read" in noise.json()["detail"]
    assert short.status_code == 400
    assert "stutter" in short.json()["detail"]

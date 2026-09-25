"""GET /v1/voices and POST /v1/voices/sample: a picker's list, and a voice heard on its key."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api._deps import a_settings
from pinecall.api.app import app
from pinecall.api.voices import the_sampler, the_shelf
from pinecall.providers.registry import Asked
from pinecall.providers.tts.sampling import Sample, SampleRefused
from pinecall.providers.tts.shelf import Shelf

pytestmark = pytest.mark.unit

THE_ORGS_KEY = "sk_car_brought_by_the_clinic"
MARTA = "de38f545-c574-44e8-9b54-a7d6fec1c6b1"
A_WAV = b"RIFF....WAVE"


class Heard:
    """What the door asked the vendor to say, and a vendor that can be made to refuse."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, Asked, str]] = []
        self.refuses = False

    async def __call__(self, vendor: str, asked: Asked, text: str) -> Sample:
        self.asked.append((vendor, asked, text))
        if self.refuses:
            raise SampleRefused("cartesia did not say it: Not Found")
        return Sample(wav=A_WAV, first_audio_ms=210, total_ms=900)


@pytest.fixture
def heard() -> Iterator[Heard]:
    said = Heard()

    def served(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-API-Key"] == THE_ORGS_KEY
        voices = [
            {"id": MARTA, "name": "Marta", "language": "es", "country": "ES", "gender": "feminine"}
        ]
        return httpx.Response(200, json={"data": voices, "has_more": False})

    shelf = Shelf(httpx.AsyncClient(transport=httpx.MockTransport(served)))
    app.dependency_overrides[the_shelf] = lambda: shelf
    app.dependency_overrides[the_sampler] = lambda: said
    yield said
    app.dependency_overrides.pop(the_shelf, None)
    app.dependency_overrides.pop(the_sampler, None)


async def brought(http: httpx.AsyncClient) -> None:
    answer = await http.put("/v1/provider-keys/cartesia", json={"key": THE_ORGS_KEY})
    assert answer.status_code == 204


async def test_the_list_is_the_vendors_on_the_orgs_own_key(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    del heard
    await brought(tenant_http)

    listed = await tenant_http.get("/v1/voices", params={"tts": "cartesia", "language": "es"})

    assert listed.status_code == 200
    assert listed.json()["voices"] == [
        {
            "id": MARTA,
            "name": "Marta",
            "language": "es",
            "description": "",
            "gender": "feminine",
            "country": "ES",
            "accent": "",
        }
    ]
    assert THE_ORGS_KEY not in listed.text


async def test_no_key_for_the_vendor_is_409_and_a_vendor_not_listed_is_404(
    tenant_http: httpx.AsyncClient, heard: Heard, settings: Settings
) -> None:
    del heard
    keyless = settings.model_copy(update={"cartesia_api_key": None})
    app.dependency_overrides[a_settings] = lambda: keyless
    no_key = await tenant_http.get("/v1/voices", params={"tts": "cartesia"})
    assert no_key.status_code == 409
    assert "cartesia" in no_key.json()["detail"]
    not_listed = await tenant_http.get("/v1/voices", params={"tts": "rime"})
    assert not_listed.status_code == 404


async def test_a_sample_is_the_wav_with_its_timing_beside_it(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    await brought(tenant_http)
    body = {"tts": "cartesia", "voice": MARTA, "model": "sonic-3", "language": "es", "text": "Hola"}

    answer = await tenant_http.post("/v1/voices/sample", json=body)

    assert answer.status_code == 200
    assert answer.headers["content-type"] == "audio/wav"
    assert answer.content == A_WAV
    assert answer.headers["server-timing"] == "first-audio;dur=210, total;dur=900"
    vendor, asked, text = heard.asked[0]
    assert (vendor, asked.voice_id, asked.model, asked.language, text) == (
        "cartesia",
        MARTA,
        "sonic-3",
        "es",
        "Hola",
    )
    assert asked.keys["cartesia"] == THE_ORGS_KEY


async def test_a_curated_name_is_sent_as_its_id(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    answer = await tenant_http.post(
        "/v1/voices/sample", json={"tts": "elevenlabs", "voice": "carolina", "text": "Hi"}
    )
    assert answer.status_code == 200
    assert heard.asked[0][1].voice_id == "EXAVITQu4vr4xnSDxMaL"


async def test_a_typo_is_422_and_a_vendor_that_says_no_is_502(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    typo = await tenant_http.post(
        "/v1/voices/sample", json={"tts": "elevenlabs", "voice": "carolin", "text": "Hi"}
    )
    assert typo.status_code == 422
    not_a_uuid = await tenant_http.post(
        "/v1/voices/sample", json={"tts": "cartesia", "voice": "nobody", "text": "Hola"}
    )
    assert not_a_uuid.status_code == 422
    heard.refuses = True
    refused = await tenant_http.post(
        "/v1/voices/sample", json={"tts": "cartesia", "voice": MARTA, "text": "Hola"}
    )
    assert refused.status_code == 502
    assert "Not Found" in refused.json()["detail"]


async def test_a_sample_is_a_sentence_and_not_a_document(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    long = await tenant_http.post(
        "/v1/voices/sample", json={"tts": "cartesia", "voice": MARTA, "text": "a" * 401}
    )
    assert long.status_code == 422
    assert heard.asked == []

"""GET /v1/voices and POST /v1/voices/sample: a picker's list, and a voice heard on its key."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.agents.voices import (
    SAMPLES_A_MINUTE,
    TEXT_CEILING,
    get_sampler,
    get_sampling,
    get_shelf,
)
from pinecall.api.app import app
from pinecall.api.deps import get_settings
from pinecall.auth.throttle import Throttle
from pinecall.providers.registry import Asked
from pinecall.providers.tts.sampling import A_LINE_FOR, Sample, SampleRefused
from pinecall.providers.tts.vendor_voices import Shelf

pytestmark = pytest.mark.unit

THE_ORGS_KEY = "sk_car_brought_by_the_clinic"
MARTA = "de38f545-c574-44e8-9b54-a7d6fec1c6b1"
A_WAV = b"RIFF....WAVE"


class Heard:
    """What the door asked the vendor to say, and a vendor that can be made to refuse."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, Asked, str]] = []
        self.refuses: SampleRefused | None = None

    async def __call__(self, vendor: str, asked: Asked, text: str) -> Sample:
        self.asked.append((vendor, asked, text))
        if self.refuses is not None:
            raise self.refuses
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
    app.dependency_overrides[get_shelf] = lambda: shelf
    app.dependency_overrides[get_sampler] = lambda: said
    throttle = Throttle(SAMPLES_A_MINUTE)
    app.dependency_overrides[get_sampling] = lambda: throttle
    yield said
    for dep in (get_shelf, get_sampler, get_sampling):
        app.dependency_overrides.pop(dep, None)


async def brought(http: httpx.AsyncClient) -> None:
    answer = await http.put("/v1/provider-keys/cartesia", json={"key": THE_ORGS_KEY})
    assert answer.status_code == 204


async def a_sample_of(http: httpx.AsyncClient, **body: object) -> httpx.Response:
    return await http.post("/v1/voices/sample", json={"tts": "cartesia", "voice": MARTA, **body})


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


async def test_no_key_for_the_vendor_is_503_and_a_vendor_not_listed_is_404(
    tenant_http: httpx.AsyncClient, heard: Heard, settings: Settings
) -> None:
    del heard
    keyless = settings.model_copy(update={"cartesia_api_key": None})
    app.dependency_overrides[get_settings] = lambda: keyless
    no_key = await tenant_http.get("/v1/voices", params={"tts": "cartesia"})
    assert no_key.status_code == 503
    assert "cartesia" in no_key.json()["detail"]
    not_listed = await tenant_http.get("/v1/voices", params={"tts": "rime"})
    assert not_listed.status_code == 404


async def test_a_sample_is_the_wav_with_its_timing_beside_it(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    await brought(tenant_http)

    answer = await a_sample_of(tenant_http, model="sonic-3", language="es", text="Hola")

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


async def test_a_sample_with_no_words_reads_one_line_in_its_language(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    assert (await a_sample_of(tenant_http, language="es-ES")).status_code == 200
    assert (await a_sample_of(tenant_http, text="")).status_code == 200
    assert [text for _, _, text in heard.asked] == [A_LINE_FOR["es"], A_LINE_FOR["en"]]


async def test_a_curated_name_is_sent_as_its_id_on_its_own_vendor(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    answer = await a_sample_of(tenant_http, tts="elevenlabs", voice="carolina", text="Hi")
    assert answer.status_code == 200
    assert heard.asked[0][1].voice_id == "EXAVITQu4vr4xnSDxMaL"


# The sample reads its three words exactly as the settings door does, so what plays is what
# saves: the same typos are refused in the same sentences, before any vendor is asked.
async def test_what_the_settings_door_refuses_the_sample_refuses_in_the_same_words(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    typo = await a_sample_of(tenant_http, tts="elevenlabs", voice="carolin", text="Hi")
    assert typo.status_code == 422 and "no voice named 'carolin'" in typo.json()["detail"]
    not_a_uuid = await a_sample_of(tenant_http, voice="nobody")
    assert not_a_uuid.status_code == 422
    no_vendor = await a_sample_of(tenant_http, tts="nobody")
    # A bare word is a model on the vendor in use, and a model Cartesia does not have is a call
    # that dies on its first line: refused here, and by the settings door, in one sentence.
    assert no_vendor.status_code == 422
    assert (
        "no cartesia model 'nobody'; this build has sonic-3, sonic-2" in no_vendor.json()["detail"]
    )
    swapped = await a_sample_of(
        tenant_http, tts="elevenlabs", voice="carolina", model="eleven_turbo_v2_5"
    )
    assert swapped.status_code == 422 and "not run here" in swapped.json()["detail"]
    two_vendors = await a_sample_of(tenant_http, tts="cartesia", voice="carolina")
    assert two_vendors.status_code == 422 and "name one vendor" in two_vendors.json()["detail"]
    assert heard.asked == []


async def test_the_vendors_own_status_is_read_as_whose_fault_it_was(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    heard.refuses = SampleRefused("cartesia did not say it: Unauthorized", 401)
    key_refused = await a_sample_of(tenant_http)
    assert key_refused.status_code == 409
    assert "refused the key this org runs on" in key_refused.json()["detail"]
    heard.refuses = SampleRefused("cartesia did not say it: Not Found", 404)
    assert (await a_sample_of(tenant_http)).status_code == 422
    heard.refuses = SampleRefused("cartesia did not say it: Bad Gateway", 502)
    assert (await a_sample_of(tenant_http)).status_code == 502
    heard.refuses = SampleRefused("cartesia did not say it: no route")
    assert (await a_sample_of(tenant_http)).status_code == 502


async def test_a_sample_is_a_sentence_and_not_a_document(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    long = await a_sample_of(tenant_http, text="a" * (TEXT_CEILING + 1))
    assert long.status_code == 422 and str(TEXT_CEILING) in long.json()["detail"]
    assert heard.asked == []


async def test_a_key_that_asks_for_samples_in_a_loop_is_told_the_ceiling(
    tenant_http: httpx.AsyncClient, heard: Heard
) -> None:
    two = Throttle(2)
    app.dependency_overrides[get_sampling] = lambda: two
    assert (await a_sample_of(tenant_http)).status_code == 200
    assert (await a_sample_of(tenant_http)).status_code == 200
    third = await a_sample_of(tenant_http)
    assert third.status_code == 429 and "a minute" in third.json()["detail"]
    assert len(heard.asked) == 2

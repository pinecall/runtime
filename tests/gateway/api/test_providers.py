"""Tests for the providers doors: the catalogue, the org's keys, the voices and a sample."""

from pinecall.providers import catalog
from pinecall.providers.catalog import Providers
from pinecall.tenancy import vault
from tests.conftest import Knocking, configured, issued, postgres
from tests.fakes.acme import ACME

PROVIDERS = "/v1/providers"
KEYS = "/v1/provider-keys"
VOICES = "/v1/voices"
SAMPLE = "/v1/voices/sample"


def with_voices(*voices: dict[str, str]) -> Providers:
    """The box's configuration with the fake vendor listing these voices."""
    row = configured().model_dump(mode="json")
    row["tuning"]["tts/acme"] = {"options": {"voices": list(voices)}}
    row["lines"] = {"es": "Hola, así sueno."}
    return Providers.model_validate(row)


@postgres
async def test_the_catalogue_says_how_this_org_may_run_each_vendor(knocking: Knocking) -> None:
    pool = knocking.gateway.connections.pool
    async with knocking.http(knocking.app["sandbox"]) as org:
        offered = await org.get(PROVIDERS)
        await org.put(f"{KEYS}/{ACME}", json={"key": "the orgs own"})
        yours = await org.get(PROVIDERS)
        brought = await org.get(KEYS)
        await org.delete(f"{KEYS}/{ACME}")
        gone = await org.delete(f"{KEYS}/{ACME}")
        nobody = await org.put(f"{KEYS}/nobody", json={"key": "x"})
        both = await org.put(f"{KEYS}/{ACME}", json={"key": "x", "credentials": {"api_key": "x"}})
    acme = next(row for row in offered.json()["providers"] if row["name"] == ACME)
    assert (acme["availability"], acme["ready"], acme["voices_listed"]) == ("offered", True, True)
    assert acme["does"] == ["llm", "stt", "tts"]
    assert (acme["standing"], acme["aliases"], acme["env"]) == ("ready", [], None)
    assert offered.json()["defaults"]["llm"] == ACME
    again = next(row for row in yours.json()["providers"] if row["name"] == ACME)
    assert again["availability"] == "yours"
    assert offered.json()["voices"] == []
    assert brought.json() == {"vendors": [ACME]}
    assert gone.status_code == 404
    assert nobody.status_code == 400
    assert "no vendor named 'nobody'" in nobody.json()["detail"]
    assert both.status_code == 400
    assert await vault.vendors_of(pool, knocking.org.id) == []


@postgres
async def test_a_key_without_the_providers_scope_reads_no_catalogue(knocking: Knocking) -> None:
    reads = await issued(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", frozenset({"calls"})
    )
    async with knocking.http(reads) as reader:
        refused = await reader.get(PROVIDERS)
    assert refused.status_code == 403


@postgres
async def test_the_voices_are_the_vendors_own_in_the_language_asked(knocking: Knocking) -> None:
    await catalog.configure(
        knocking.gateway.connections.pool,
        with_voices(
            {"id": "v-ana", "name": "Ana", "language": "es-ES", "gender": "female"},
            {"id": "v-joe", "name": "Joe", "language": "en-US", "gender": "male"},
        ),
    )
    async with knocking.http(knocking.app["sandbox"]) as org:
        spanish = await org.get(VOICES, params={"tts": ACME, "language": "es"})
        every = await org.get(VOICES, params={"tts": ACME})
        nobody = await org.get(VOICES, params={"tts": "nobody"})
    assert spanish.status_code == 200
    assert [voice["id"] for voice in spanish.json()["voices"]] == ["v-ana"]
    assert spanish.json()["voices"][0]["gender"] == "female"
    assert [voice["id"] for voice in every.json()["voices"]] == ["v-ana", "v-joe"]
    assert nobody.status_code == 400


@postgres
async def test_a_vendor_whose_plugin_lists_none_is_listed_from_the_row(knocking: Knocking) -> None:
    row = configured().model_dump(mode="json")
    row["listed"] = {"deepgram/es": [{"id": "v-marta", "name": "Marta", "country": "ES"}]}
    await catalog.configure(knocking.gateway.connections.pool, Providers.model_validate(row))
    async with knocking.http(knocking.app["sandbox"]) as org:
        catalogue = await org.get(PROVIDERS)
        spanish = await org.get(VOICES, params={"tts": "deepgram", "language": "es-ES"})
        english = await org.get(VOICES, params={"tts": "deepgram", "language": "en"})
    deepgram = next(item for item in catalogue.json()["providers"] if item["name"] == "deepgram")
    assert deepgram["voices_listed"] is True
    assert [(voice["id"], voice["country"]) for voice in spanish.json()["voices"]] == [
        ("v-marta", "ES")
    ]
    assert english.status_code == 404
    assert "deepgram lists no voices here" in english.json()["detail"]


@postgres
async def test_a_sample_is_the_line_said_as_wav_with_its_timing(knocking: Knocking) -> None:
    await catalog.configure(knocking.gateway.connections.pool, with_voices())
    async with knocking.http(knocking.app["sandbox"]) as org:
        heard = await org.post(SAMPLE, json={"tts": ACME, "voice": "v-ana", "language": "es"})
        too_long = await org.post(SAMPLE, json={"tts": ACME, "voice": "v-ana", "text": "x" * 401})
    assert heard.status_code == 200
    assert heard.headers["content-type"] == "audio/wav"
    assert heard.content[:4] == b"RIFF"
    assert heard.headers["server-timing"].startswith("first-audio;dur=")
    assert too_long.status_code == 400
    assert "400" in too_long.json()["detail"]


@postgres
async def test_a_key_past_its_samples_for_the_minute_waits(knocking: Knocking) -> None:
    await catalog.configure(knocking.gateway.connections.pool, with_voices())
    knocking.gateway.samples.tries = 2
    async with knocking.http(knocking.app["sandbox"]) as org:
        answers = [
            (await org.post(SAMPLE, json={"tts": ACME, "voice": "v-ana"})).status_code
            for _ in range(3)
        ]
    assert answers == [200, 200, 429]


# The object an org brings names secrets, never where the box sends them.
@postgres
async def test_an_orgs_credentials_object_may_name_no_address(knocking: Knocking) -> None:
    pointed = {"credentials": {"api_key": "k", "base_url": "http://pinecall-gateway:8080/"}}
    async with knocking.http(knocking.app["sandbox"]) as org:
        refused = await org.put(f"{KEYS}/{ACME}", json=pointed)
        kept = await org.put(f"{KEYS}/{ACME}", json={"credentials": {"api_key": "k"}})
        listed = await org.get(KEYS)
    assert refused.status_code == 400
    assert "operator's to set" in refused.json()["detail"]
    assert kept.status_code == 204
    assert listed.json()["vendors"] == [ACME]

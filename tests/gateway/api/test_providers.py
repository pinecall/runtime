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
    assert offered.json()["defaults"]["llm"] == ACME
    again = next(row for row in yours.json()["providers"] if row["name"] == ACME)
    assert again["availability"] == "yours"
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

"""The agent's two pipeline doors, over the real ASGI app: what is measured, and what is turned."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.providers.overrides import BLANK, NOT_RUN_HERE
from pinecall.providers.tts.elevenlabs import DEFAULT_MODEL
from pinecall.providers.tts.voices import VOICES, known_voices, voice_names
from pinecall.worker.client import Gateway
from tests.api.conftest import AGENT, PIPELINE, PIPELINE_KNOBS
from tests.api.pipeline.conftest import declared

pytestmark = pytest.mark.unit


async def a_call_that_measured(store: MemoryStore, call: str, *seconds: float) -> None:
    """One finished call whose agent turns each carried an end-to-end latency."""
    for took in seconds:
        await store.append(call, AGENT, "turn.agent", {"metrics": {"e2e_latency": took}})


async def test_the_pipeline_names_the_vendor_each_of_the_three_stages_runs(
    fleet_http: httpx.AsyncClient, registry: Registry
) -> None:
    await declared(registry)
    said = (await fleet_http.get(PIPELINE)).json()
    assert said["hears"]["vendor"] == "soniox"
    assert (said["decides"]["vendor"], said["decides"]["model"]) == (
        "anthropic",
        "claude-haiku-4-5",
    )
    assert (said["speaks"]["vendor"], said["speaks"]["voice_id"]) == (
        "elevenlabs",
        "a-declared-voice",
    )
    assert said["greeting"] == "Clínica Norte, buenas."


async def test_the_medians_are_taken_over_every_turn_of_the_agents_last_calls(
    fleet_http: httpx.AsyncClient, registry: Registry, store: MemoryStore
) -> None:
    await declared(registry)
    await a_call_that_measured(store, "call_one", 1.0, 2.0)
    await a_call_that_measured(store, "call_two", 3.0)
    said = (await fleet_http.get(PIPELINE)).json()
    assert said["calls"] == 2
    assert said["medians"] == [{"name": "e2e_latency", "seconds": 2.0, "turns": 3}]


async def test_an_agent_no_app_is_holding_is_a_refusal_and_never_an_empty_pipeline(
    fleet_http: httpx.AsyncClient,
) -> None:
    answered = await fleet_http.get("/v1/agents/nobody/pipeline")
    assert answered.status_code == 404


# The criterion, proven without a telephone: the override an operator PUTs is on the config the
# worker reads to build the next session, off the one door an agent's config has ever travelled.
# And it is a NAME that is PUT: the worker is handed the id the table holds for it, never the word.
async def test_a_voice_name_turned_here_reaches_the_worker_as_the_id_the_vendor_knows(
    fleet_http: httpx.AsyncClient, registry: Registry, worker_gateway: Gateway
) -> None:
    await declared(registry)
    turned = await fleet_http.put(PIPELINE_KNOBS, json={"voice": "mateo"})
    assert turned.status_code == 200
    config = await worker_gateway.agent(AGENT)
    assert config.voice is not None
    assert config.voice.voice_id == VOICES["mateo"].voice_id
    assert config.voice.voice_id != "mateo"
    assert config.voice.provider == "elevenlabs"


# The 1008 door, closed on the operator's side: what an app cannot declare, an operator cannot PUT.
async def test_a_voice_no_one_curated_is_refused_in_the_tables_own_sentence(
    fleet_http: httpx.AsyncClient, registry: Registry, worker_gateway: Gateway
) -> None:
    await declared(registry)
    refused = await fleet_http.put(PIPELINE_KNOBS, json={"voice": "carolinaa"})
    assert refused.status_code == 400
    assert (
        refused.json()["detail"]
        == f"no voice named 'carolinaa'; this build knows: {known_voices()}"
    )
    config = await worker_gateway.agent(AGENT)
    assert config.voice is not None
    assert config.voice.voice_id == "a-declared-voice"


async def test_the_pipeline_door_offers_the_names_the_table_curates_and_no_second_list(
    fleet_http: httpx.AsyncClient, registry: Registry
) -> None:
    await declared(registry)
    said = (await fleet_http.get(PIPELINE)).json()
    assert said["voices"] == list(voice_names())


async def test_a_knob_left_out_of_the_next_body_goes_back_to_what_the_app_declared(
    fleet_http: httpx.AsyncClient, registry: Registry, worker_gateway: Gateway
) -> None:
    await declared(registry)
    await fleet_http.put(PIPELINE_KNOBS, json={"voice": "mateo"})
    await fleet_http.put(PIPELINE_KNOBS, json={"greeting": "Buenas, Clínica Norte."})
    config = await worker_gateway.agent(AGENT)
    assert config.voice is not None
    assert config.voice.voice_id == "a-declared-voice"
    assert config.greeting == "Buenas, Clínica Norte."


# convo ms-14: an empty voice reached the vendor and a line of calls went out silent.
async def test_a_blank_voice_is_refused_with_the_sentence_that_says_why(
    fleet_http: httpx.AsyncClient, registry: Registry, worker_gateway: Gateway
) -> None:
    await declared(registry)
    refused = await fleet_http.put(PIPELINE_KNOBS, json={"voice": "   "})
    assert refused.status_code == 400
    assert refused.json()["detail"] == BLANK.format(field="voice")
    config = await worker_gateway.agent(AGENT)
    assert config.voice is not None
    assert config.voice.voice_id == "a-declared-voice"


async def test_a_tts_model_this_build_does_not_run_is_refused_in_the_providers_own_words(
    fleet_http: httpx.AsyncClient, registry: Registry
) -> None:
    await declared(registry)
    refused = await fleet_http.put(PIPELINE_KNOBS, json={"tts_model": "eleven_turbo_v2_5"})
    assert refused.status_code == 400
    assert refused.json()["detail"] == NOT_RUN_HERE.format(
        asked="eleven_turbo_v2_5", instead=DEFAULT_MODEL
    )


async def test_a_vendor_this_build_has_no_file_for_is_a_typo_and_is_refused(
    fleet_http: httpx.AsyncClient, registry: Registry
) -> None:
    await declared(registry)
    refused = await fleet_http.put(PIPELINE_KNOBS, json={"llm": "openai-but-misspelt/gpt-5"})
    assert refused.status_code == 400
    assert "no llm vendor named" in refused.json()["detail"]


async def test_a_bare_model_keeps_the_vendor_the_agent_is_already_running_on(
    fleet_http: httpx.AsyncClient, registry: Registry, worker_gateway: Gateway
) -> None:
    await declared(registry)
    await fleet_http.put(PIPELINE_KNOBS, json={"llm": "claude-sonnet-4-5"})
    config = await worker_gateway.agent(AGENT)
    assert config.llm is not None
    assert (config.llm.provider, config.llm.model) == ("anthropic", "claude-sonnet-4-5")

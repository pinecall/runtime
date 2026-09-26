"""The agent's pipeline door, over the real ASGI app: what it runs on, and what is measured."""

from __future__ import annotations

import httpx
import pytest

from pinecall.live.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.orgs.tuning_store_memory import MemoryTuning
from pinecall.providers.tts.curated_voices import VOICES, voice_names
from tests.api.agents.pipeline.conftest import declared
from tests.api.conftest import AGENT, PIPELINE

pytestmark = pytest.mark.unit


async def a_call_that_measured(store: MemoryStore, call: str, *seconds: float) -> None:
    """One finished call whose agent turns each carried an end-to-end latency."""
    for took in seconds:
        await store.append(call, AGENT, "turn.agent", {"metrics": {"e2e_latency": took}})


async def test_the_pipeline_names_the_vendor_each_of_the_three_stages_runs(
    fleet_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    await declared(registry, tuning)
    said = (await fleet_http.get(PIPELINE)).json()
    assert said["hears"]["vendor"] == "deepgram"
    assert (said["decides"]["vendor"], said["decides"]["model"]) == (
        "anthropic",
        "claude-haiku-4-5",
    )
    assert (said["speaks"]["vendor"], said["speaks"]["voice_id"]) == (
        "elevenlabs",
        VOICES["mateo"].voice_id,
    )
    assert said["greeting"] == {
        "say": "Clínica Norte, buenas.",
        "reply": None,
        "allow_interruptions": None,
    }


async def test_the_medians_are_taken_over_every_turn_of_the_agents_last_calls(
    fleet_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning, store: MemoryStore
) -> None:
    await declared(registry, tuning)
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


async def test_the_pipeline_door_offers_the_names_the_table_curates_and_no_second_list(
    fleet_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    await declared(registry, tuning)
    said = (await fleet_http.get(PIPELINE)).json()
    assert said["voices"] == list(voice_names())


async def test_the_pipeline_report_lists_every_vendor_a_stage_could_be_turned_onto(
    fleet_http: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    """The screen that changes a stage is where a person needs to see that Cartesia exists."""
    await declared(registry, tuning)
    report = (await fleet_http.get(PIPELINE)).json()
    names = {row["name"] for row in report["providers"]}
    assert len(names) > 40
    assert {"cartesia", "rime", "livekit"} <= names
    assert report["defaults"]["stt"] == "deepgram"
    assert report["models"]["llm/anthropic"][0] == "claude-haiku-4-5-20251001"

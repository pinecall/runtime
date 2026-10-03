"""Tests for the worker process: registering under its fleet, the overflow's gate and its end."""

import time

import pytest
from livekit.agents import AgentServer, Plugin

from pinecall.domain.errors import SettingsRefused
from pinecall.log import queries
from pinecall.process.settings import Settings
from pinecall.providers.build import installed
from pinecall.wire.events import CallEnded
from pinecall.wire.rest.calls import OpenCallRequest
from pinecall.worker._job import ended_and_sealed, writer_of
from pinecall.worker.main import (
    CLOSED,
    INITIALIZE_S,
    OPEN,
    OverflowGate,
    overflow_of,
    run,
    sentence_entry,
    server_of,
)
from tests.conftest import AGENT, Knocking, postgres
from tests.fleet.test_client import (
    LosingTheFirstBatchAnswer,
    a_call,
    custom,
    fleet_client,
    losing_client,
)

REGISTRABLE = {
    "LIVEKIT_URL": "ws://127.0.0.1:7880",
    "LIVEKIT_API_KEY": "APIfake",
    "LIVEKIT_API_SECRET": "fake-secret",
    "PINECALL_FLEET": "pinecall-sandbox",
}


def settings_with(**told: str) -> Settings:
    """Settings of a worker that could register, with what the test changes."""
    return Settings.model_validate({**REGISTRABLE, **told})


def test_a_worker_registers_under_its_own_name_in_its_fleet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    named: list[str] = []
    original = AgentServer.rtc_session

    def recorded(
        server: AgentServer, *args: object, agent_name: str = "", **told: object
    ) -> object:
        named.append(agent_name)
        return original(server, *args, agent_name=agent_name, **told)

    monkeypatch.setattr(AgentServer, "rtc_session", recorded)
    server_of(settings_with(PINECALL_WORKER_NAME="w-7"))
    assert named == ["pinecall-sandbox/w-7"]


def test_both_servers_give_a_new_process_the_time_the_plugins_take(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    given: list[object] = []
    original = AgentServer.__init__

    def recorded(server: AgentServer, *args: object, **told: object) -> None:
        given.append(told.get("initialize_process_timeout"))
        original(server, *args, **told)

    monkeypatch.setattr(AgentServer, "__init__", recorded)
    server_of(settings_with())
    overflow_of(settings_with(), OverflowGate())
    assert given == [INITIALIZE_S, INITIALIZE_S]


# LiveKit's line is LiveKit's: the worker reports on its scale and overrides nothing.
def test_neither_worker_overrides_livekits_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    given: list[object] = []
    original = AgentServer.__init__

    def recorded(server: AgentServer, *args: object, **told: object) -> None:
        given.append(told.get("load_threshold"))
        original(server, *args, **told)

    monkeypatch.setattr(AgentServer, "__init__", recorded)
    server_of(settings_with(PINECALL_MAX_JOBS="8"))
    server_of(settings_with())
    assert given == [None, None]


def test_both_servers_register_every_plugin_for_livekits_preload() -> None:
    preloaded: list[set[str]] = []
    for build in (
        lambda: server_of(settings_with()),
        lambda: overflow_of(settings_with(), OverflowGate()),
    ):
        installed.cache_clear()
        build()
        assert installed.cache_info().currsize == 1
        preloaded.append({plugin.package for plugin in Plugin.registered_plugins})
    for packages in preloaded:
        assert {
            f"livekit.plugins.{vendor}" for vendor in ("anthropic", "deepgram", "cartesia")
        } <= packages


# livekit's drain raises at its timeout; the close after it is what seals the calls still up.
async def test_a_drain_that_runs_out_still_closes_the_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[AgentServer] = []

    async def stopped_at_once(_server: AgentServer) -> None:
        return None

    async def out_of_time(_server: AgentServer, _timeout: int) -> None:
        raise TimeoutError

    async def closing(server: AgentServer) -> None:
        closed.append(server)

    def no_jobs(_server: AgentServer) -> list[object]:
        return []

    # A server that never ran has no process pool to count the jobs of.
    monkeypatch.setattr(AgentServer, "active_jobs", property(no_jobs))
    monkeypatch.setattr(AgentServer, "run", stopped_at_once)
    monkeypatch.setattr(AgentServer, "drain", out_of_time)
    monkeypatch.setattr(AgentServer, "aclose", closing)
    assert await run(settings_with(PINECALL_GATEWAY_URL="http://127.0.0.1:9")) == 0
    assert len(closed) == 1


def test_a_worker_without_its_livekit_pair_is_refused() -> None:
    with pytest.raises(SettingsRefused, match="LIVEKIT_API_SECRET"):
        server_of(settings_with(LIVEKIT_API_SECRET=""))


def test_the_overflow_is_closed_until_the_fleet_is_full() -> None:
    gate = OverflowGate()
    server = overflow_of(settings_with(), gate)
    assert isinstance(server, AgentServer)
    assert gate(server) == CLOSED
    gate.fleet_is_full = True
    assert gate(server) == OPEN


SAYS = "All our agents are busy: we will call you back."


# The overflow opens its call and is its one writer: the sentence, then call.ended, then the seal.
@postgres
async def test_the_overflows_sentence_and_end_are_written_once_when_an_answer_is_lost(
    knocking: Knocking,
) -> None:
    losing = LosingTheFirstBatchAnswer()
    client = losing_client(knocking, losing)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    writing = writer_of(client, context.call)
    writing.write("agent.transcript", sentence_entry(SAYS))
    ended = CallEnded(
        reason="agent_hung_up", ended_by="agent", ended_at=time.time(), duration_s=1.0
    )
    await ended_and_sealed(client, writing, ended, SAYS)
    store = knocking.gateway.logs.store
    kinds = [entry.type for entry in await store.whole(context.call)]
    assert losing.lost == 1
    # The sentence is ephemeral, as the wire says: it takes its seq and is not kept.
    assert kinds.count("call.ended") == 1
    assert await store.written(context.call) == 2
    assert await store.sealed(context.call)
    await client.aclose()
    await losing.real.aclose()


# The dead worker's writer took three; the told job's follows on from the count its dispatch
# carries, as the gateway read it off the call's head.
@postgres
async def test_the_told_job_takes_the_call_over_where_its_dead_worker_stopped(
    knocking: Knocking,
) -> None:
    worker = fleet_client(knocking)
    context = a_call(knocking)
    await worker.open(OpenCallRequest(agent=AGENT, context=context))
    dead = writer_of(worker, context.call)
    for name in ("uno", "dos", "tres"):
        dead.write("custom", custom(name))
    await dead.close(5)
    kept = await queries.scope_of_call(knocking.gateway.connections.pool, context.call)
    assert kept is not None
    losing = LosingTheFirstBatchAnswer()
    told_job = losing_client(knocking, losing)
    writing = writer_of(told_job, context.call, after=kept.written)
    sentence = await writing.write("agent.transcript", sentence_entry(SAYS))
    await writing.close(5)
    assert kept.written == 3
    assert losing.lost == 1
    # call.ringing is the gateway's 1, the dead worker's three are 2 to 4.
    assert sentence.seq == 5
    assert writing.refused == []
    assert await knocking.gateway.logs.store.written(context.call) == 4
    await worker.aclose()
    await told_job.aclose()
    await losing.real.aclose()

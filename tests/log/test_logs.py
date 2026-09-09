"""The two logs a runtime writes: what append() does, in what order, and where it stops."""

import pytest

from pinecall.log.logs import AgentLog, CallLog
from pinecall.log.pii import MASK, Masker
from pinecall.log.store import LogSealed, MemoryStore
from pinecall.types.agent import AgentConfig
from pinecall.types.json import JsonObject

pytestmark = pytest.mark.unit

AGENT = "clinica-norte"
CALL = "CA_1"

A_SUMMARY: JsonObject = {
    "reason": "hangup",
    "outcome": "booked",
    "duration_s": 61.4,
    "turns": 5,
    "usage": [],
    "cost": {"total": 0.0},
}

# The entry a call ends on: the judges' verdict, written after the summary. It is the terminal
# event, so it is the one that seals — docs/decisions/scoring.md.
A_SCORE: JsonObject = {"passed": True, "judges": [], "judge_calls": 0}


async def test_an_append_carries_the_call_and_the_agent_into_the_store(
    memory_store: MemoryStore,
) -> None:
    log = CallLog(memory_store, AGENT, CALL)
    entry = await log.append("call.started", {"channel": "phone"})
    assert (entry.call, entry.agent, entry.seq) == (CALL, AGENT, 1)
    assert await memory_store.since(CALL) == [entry]


async def test_the_protocol_decides_what_is_ephemeral_and_the_caller_may_overrule_it(
    memory_store: MemoryStore,
) -> None:
    log = CallLog(memory_store, AGENT, CALL)
    assert (await log.append("user.transcript", {"text": "ho"})).ephemeral
    assert not (await log.append("turn.user", {"text": "hola"})).ephemeral
    assert not (await log.append("user.transcript", {"text": "ho"}, ephemeral=False)).ephemeral


async def test_every_live_reader_hears_the_entry_the_store_already_has(
    memory_store: MemoryStore,
) -> None:
    """In that order: a reader can never see an entry the store does not have."""
    log = CallLog(memory_store, AGENT, CALL)
    reader = log.subscribe()
    entry = await log.append("custom", {"name": "n", "data": {}})
    assert (await anext(reader)).seq == entry.seq
    assert await memory_store.latest_seq(CALL) == 1


async def test_the_masker_runs_before_the_store_sees_the_data(
    memory_store: MemoryStore,
) -> None:
    config = AgentConfig(slug=AGENT, state_fields={"patient": "pii"})
    log = CallLog(memory_store, AGENT, CALL, masker=Masker(config))
    await log.append("state.changed", {"state": {"patient": "Marta Ruiz"}, "changed": ["patient"]})
    await log.append("custom", {"name": "n", "data": {"note": "Marta Ruiz llamó"}})
    written = await memory_store.since(CALL)
    assert written[1].data["data"]["note"] == f"{MASK} llamó", "the store never held the name"


async def test_the_terminal_entry_seals_the_log(memory_store: MemoryStore) -> None:
    """call.score is the last thing true of a call, so nothing may follow it. Not even by bug."""
    log = CallLog(memory_store, AGENT, CALL)
    summary = await log.append("call.summary", A_SUMMARY)
    assert summary.seq == 1 and not log.sealed, "the verdict on the call comes after its summary"
    assert (await log.append("call.score", A_SCORE)).seq == 2 and log.sealed
    with pytest.raises(LogSealed, match="has ended"):
        await log.append("custom", {"name": "late", "data": {}})


async def test_sealing_closes_the_live_readers_too(memory_store: MemoryStore) -> None:
    log = CallLog(memory_store, AGENT, CALL)
    reader = log.subscribe()
    await log.append("call.summary", A_SUMMARY)
    await log.append("call.score", A_SCORE)
    assert [entry.type async for entry in reader] == ["call.summary", "call.score"]


async def test_the_agents_own_log_writes_outside_every_call_and_never_seals(
    memory_store: MemoryStore,
) -> None:
    """Registered, configured, an error at three in the morning: the agent's life, not a call's."""
    log = AgentLog(memory_store, AGENT)
    registered = await log.append("agent.registered", {"routes": []})
    assert registered.call is None and registered.seq == 1
    assert not hasattr(log, "seal"), "an agent's log has no end while the agent exists"
    await CallLog(memory_store, AGENT, CALL).append("custom", {"name": "n", "data": {}})
    assert await log.calls() == [CALL]
    assert [entry.type for entry in await log.since()] == ["agent.registered"]

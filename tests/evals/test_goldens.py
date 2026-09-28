"""Tests for a golden played on a written call."""

import asyncio
import time

import pytest

from pinecall.domain.agent import AgentConfig, Greeting
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import Json, JsonObject
from pinecall.evals import goldens
from pinecall.evals.goldens import A_GOLDEN, drive, events_after, golden_lookup, settled
from pinecall.log.logs import Fanout, Log
from pinecall.log.store import Store
from pinecall.wire.frames import Entry
from pinecall.wire.parts import PlatformTool
from pinecall.wire.rest.evals import Golden
from tests.conftest import postgres
from tests.session.conftest import AGENT, Box, a_session

A_FACT = "Prefiere que le llamen por la mañana"


A_TICK = Entry(seq=1, ts=1.0, call="c", agent=AGENT, type="user.state", ephemeral=True, data={})


FREED = AgentConfig(slug=AGENT, events={"slot_freed": frozenset({"app"})})


class Index:
    """The real index as a golden's lookup reaches it: what it was asked, and nothing found."""

    def __init__(self) -> None:
        """Asked nothing yet."""
        self.asked_for: list[PlatformTool] = []

    async def lookup(
        self, tool: PlatformTool, _arguments: JsonObject, _speech: str | None
    ) -> JsonObject:
        """Nothing found, and the tool kept."""
        self.asked_for.append(tool)
        return {"chunks": []}


def a_golden(**written: object) -> Golden:
    return Golden.model_validate({"name": "reserva", "input": ["hola"], **written})


def test_the_facts_injected_after_a_turn_come_in_the_order_the_golden_lists_them() -> None:
    golden = a_golden(
        events=[
            {"after_turn": 1, "name": "b"},
            {"name": "a"},
            {"after_turn": 1, "name": "c"},
        ]
    )
    assert [event.name for event in events_after(golden, 1)] == ["b", "c"]
    assert [event.name for event in events_after(golden, 0)] == ["a"]


async def test_a_recall_is_answered_with_the_goldens_own_facts() -> None:
    index = Index()
    found = await golden_lookup([A_FACT], index.lookup)("recall", {}, None)
    assert found == {"facts": [{"text": A_FACT, "source": A_GOLDEN}]}


async def test_the_gateway_is_never_asked_to_recall_for_a_golden() -> None:
    index = Index()
    await golden_lookup([A_FACT], index.lookup)("recall", {}, None)
    assert index.asked_for == []


async def test_a_search_is_the_real_index_because_that_is_what_a_golden_is_asking() -> None:
    index = Index()
    found = await golden_lookup([A_FACT], index.lookup)("search", {"query": "x"}, None)
    assert found == {"chunks": []}
    assert index.asked_for == ["search"]


def test_a_golden_that_seeds_nothing_reads_as_seeding_nothing() -> None:
    assert a_golden().memory == []


async def test_a_log_that_stays_quiet_is_settled_at_once_and_a_busy_one_at_most_in_the_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fanout = Fanout()
    quiet = fanout.subscribe()
    began = time.monotonic()
    await settled(quiet, quiet_s=0.01)
    assert time.monotonic() - began < 0.5
    monkeypatch.setattr(goldens, "AT_MOST_S", 0.05)
    busy = fanout.subscribe()

    async def chatter() -> None:
        while True:
            fanout.publish(A_TICK)
            await asyncio.sleep(0.001)

    talking = asyncio.create_task(chatter())
    began = time.monotonic()
    await settled(busy, quiet_s=0.02)
    talking.cancel()
    assert time.monotonic() - began < 0.5


async def test_a_log_that_ended_is_settled() -> None:
    fanout = Fanout()
    ended = fanout.subscribe()
    fanout.close()
    await settled(ended)


async def played(
    store: Store, call: str, golden: Golden, config: AgentConfig, *replies: list[Json]
) -> tuple[goldens.Played, list[str]]:
    box = Box(Log(store, call, AGENT))
    session = a_session(box, config, *replies, run="run_1")
    heard = box.log.fanout.subscribe()
    result = await drive(session, golden, heard, is_held=lambda: True)
    return result, [entry.type for entry in await store.whole(call)]


@postgres
async def test_a_golden_run_writes_no_opening_turn_at_all(
    store: Store, call: str, acme: str
) -> None:
    del acme
    greets = AgentConfig(slug=AGENT, greeting=Greeting(say="Clínica Norte, buenos días."))
    _, kinds = await played(store, call, a_golden(), greets, ["hola"])
    assert kinds.index("turn.user") < kinds.index("turn.agent")


@postgres
async def test_the_goldens_state_is_the_one_the_call_opens_in(
    store: Store, call: str, acme: str
) -> None:
    del acme
    golden = a_golden(state={"stage": "book", "patient": {"id": "p-1"}})
    _, kinds = await played(store, call, golden, FREED, ["hola"])
    entries = await store.whole(call)
    seeded = next(entry for entry in entries if entry.type == "state.changed")
    assert seeded.data["state"] == {"stage": "book", "patient": {"id": "p-1"}}
    assert kinds.index("state.changed") < kinds.index("turn.user")


@postgres
async def test_a_golden_with_an_event_injects_it_at_the_declared_turn(
    store: Store, call: str, acme: str
) -> None:
    del acme
    golden = a_golden(
        input=["hola", "¿hay algo antes?"],
        events=[{"after_turn": 1, "name": "slot_freed", "data": {"at": "10:15"}}],
    )
    result, kinds = await played(
        store, call, golden, FREED, ["Buenos días."], ["Se liberó a las 10:15."]
    )
    callers = [index for index, kind in enumerate(kinds) if kind == "turn.user"]
    answers = [index for index, kind in enumerate(kinds) if kind == "turn.agent"]
    assert answers[0] < kinds.index("event.received") < callers[1]
    assert result.held
    assert len(result.requests) == 2


@postgres
async def test_a_golden_whose_event_the_agent_never_declared_is_refused_by_name_and_ended(
    store: Store, call: str, acme: str
) -> None:
    del acme
    golden = a_golden(events=[{"name": "meteorite"}])
    with pytest.raises(DeclarationRefused, match="meteorite"):
        await played(store, call, golden, FREED, ["hola"])
    ended = [entry for entry in await store.whole(call) if entry.type == "call.ended"]
    assert [entry.data["reason"] for entry in ended] == ["error"]


@postgres
async def test_a_golden_whose_app_let_go_ends_as_app_detached_and_not_on_a_timeout(
    store: Store, call: str, acme: str
) -> None:
    del acme
    box = Box(Log(store, call, AGENT))
    session = a_session(box, FREED, ["hola"], run="run_1")
    result = await drive(session, a_golden(), box.log.fanout.subscribe(), is_held=lambda: False)
    assert not result.held
    ended = [entry for entry in await store.whole(call) if entry.type == "call.ended"]
    assert [(entry.data["reason"], entry.data["ended_by"]) for entry in ended] == [
        ("app_detached", "platform")
    ]


@postgres
async def test_a_golden_played_to_its_end_is_a_caller_who_hung_up(
    store: Store, call: str, acme: str
) -> None:
    del acme
    _, kinds = await played(store, call, a_golden(), FREED, ["hola"])
    entries = await store.whole(call)
    assert kinds[-1] == "call.score"
    ended = next(entry for entry in entries if entry.type == "call.ended")
    assert (ended.data["reason"], ended.data["ended_by"]) == ("caller_hung_up", "caller")

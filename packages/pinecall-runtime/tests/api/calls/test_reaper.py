"""The reaper: a spoken call whose room the SFU has lost is ended here, and nothing else is."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest
from starlette.testclient import TestClient

from pinecall.api.calls.reaper import NOT_JUDGED, QUIET_S, Reaper, reap_forever
from pinecall.live.calls import Live
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types import AgentConfig
from tests.api.conftest import A_RECORD
from tests.api.talking import a_context as a_call_on
from tests.api.talking import got
from tests.routes.fakes import MemoryRooms
from tests.support.clocks import Clock

pytestmark = pytest.mark.unit

THE_AGENT = "clinica-norte"
THE_DAY = date(2026, 9, 17)
# The clock every call of this module is written on, and the moment a pass is run at: an hour
# later, which is longer than QUIET_S by far.
NOON = datetime(THE_DAY.year, THE_DAY.month, THE_DAY.day, 12, tzinfo=UTC).timestamp()
AN_HOUR_LATER = NOON + 60 * 60


@pytest.fixture
def clock() -> Clock:
    return Clock(NOON, tick=1.0)


@pytest.fixture
def store(clock: Clock) -> MemoryStore:
    return MemoryStore(clock=clock)


@pytest.fixture
def logs(store: MemoryStore) -> Logs:
    return Logs(store)


@pytest.fixture
def rooms() -> MemoryRooms:
    """The SFU: empty, so every call of a test is one the rooms no longer hold."""
    return MemoryRooms()


@pytest.fixture
def reaper(store: MemoryStore, logs: Logs, rooms: MemoryRooms, live: Live) -> Reaper:
    return Reaper(store, logs, rooms, live)


async def a_call(
    store: MemoryStore,
    call: str,
    *,
    channel: str = "web",
    said: str = "Clínica Norte, buenos días.",
    ended: bool = False,
    sealed: bool = False,
) -> str:
    """One spoken call as a worker writes it, stopping wherever the test says it stopped."""
    await store.owned(call, THE_AGENT, A_RECORD.org, "production", "")
    await store.append(call, THE_AGENT, "call.started", {"channel": channel, "from": "+34600"})
    # A web call is spoken once its room opened: the fact the reaper's question turns on.
    await store.append(call, THE_AGENT, "room.opened", {"room": call})
    await store.append(
        call,
        THE_AGENT,
        "turn.agent",
        {"speech_id": "s1", "text": said, "interrupted": False, "metrics": {}},
    )
    await store.append(call, THE_AGENT, "user.state", {"state": "away"})
    if ended:
        await store.append(
            call,
            THE_AGENT,
            "call.ended",
            {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 1.0, "duration_s": 1.0},
        )
    if sealed:
        await store.append(call, THE_AGENT, "call.summary", {"outcome": "ok", "cost": {"eur": 0.1}})
        await store.append(call, THE_AGENT, "call.score", {"judges": [], "judge_calls": 0})
        await store.seal(call)
    return call


async def test_a_call_whose_room_is_gone_is_ended_as_drained_and_sealed(
    reaper: Reaper, store: MemoryStore
) -> None:
    call = await a_call(store, "CA_orphan")
    quiet_for = AN_HOUR_LATER - (await store.since(call))[-1].ts

    assert await reaper.a_pass(AN_HOUR_LATER) == [call]

    entries = await store.since(call)
    assert [entry.type for entry in entries[-3:]] == [
        "call.ended",
        "call.summary",
        "call.score",
    ]
    ended, summary, score = (entry.data for entry in entries[-3:])
    assert (ended["reason"], ended["ended_by"]) == ("drained", "platform")
    # The clock is the last thing the call SAID, not the hour of silence after it: a call reaped
    # an hour later did not last an hour.
    assert ended["ended_at"] == entries[-4].ts
    assert quiet_for > QUIET_S and ended["duration_s"] < quiet_for
    assert (summary["reason"], summary["outcome"]) == ("drained", "Clínica Norte, buenos días.")
    assert summary["usage"] == [] and summary["turns"] == 1
    assert score["not_judged"] == NOT_JUDGED and score["judges"] == []
    assert await store.unsealed_spoken(AN_HOUR_LATER, 10) == [], "the log is sealed"


async def test_a_call_with_an_agent_still_in_its_room_is_left_alone(
    reaper: Reaper, store: MemoryStore, rooms: MemoryRooms
) -> None:
    """A caller can be silent for an hour. While they are, the agent is there and this is not it."""
    call = await a_call(store, "CA_quiet_but_live")
    rooms.open.add(call)
    written = [entry.type for entry in await store.since(call)]
    assert await reaper.a_pass(AN_HOUR_LATER) == []
    assert [entry.type for entry in await store.since(call)] == written, "not a word was added"
    assert [one.call for one in await store.unsealed_spoken(AN_HOUR_LATER, 10)] == [call]


async def test_a_room_only_people_are_left_in_is_nobodys_call_and_is_closed(
    reaper: Reaper, store: MemoryStore, rooms: MemoryRooms
) -> None:
    """The job was killed; the caller's tab and a supervisor's seat kept the room up for hours."""
    call = await a_call(store, "CA_people_but_no_agent")
    rooms.agentless.add(call)
    assert await reaper.a_pass(AN_HOUR_LATER) == [call]
    assert [entry.type for entry in await store.since(call)][-1] == "call.score"
    assert rooms.taken_down == [call], "whoever was left in the room is told it is over"


async def test_a_call_that_has_only_just_gone_quiet_is_left_alone(
    reaper: Reaper, store: MemoryStore
) -> None:
    """The room may not exist YET: a log opens a moment before the SFU has anything to answer."""
    call = await a_call(store, "CA_young")
    just_now = (await store.since(call))[-1].ts + QUIET_S / 2
    assert await reaper.a_pass(just_now) == []
    assert [one.call for one in await store.unsealed_spoken(AN_HOUR_LATER, 10)] == [call]


async def test_a_call_that_ended_properly_is_never_looked_at(
    reaper: Reaper, store: MemoryStore
) -> None:
    call = await a_call(store, "CA_finished", ended=True, sealed=True)
    before = await store.since(call)
    assert await reaper.a_pass(AN_HOUR_LATER) == []
    assert await store.since(call) == before


async def a_written_call(store: MemoryStore, call: str, channel: str) -> str:
    """A chat or a WhatsApp thread: no room, started, run by a gateway that may have restarted."""
    await store.owned(call, THE_AGENT, A_RECORD.org, "production", "")
    await store.append(call, THE_AGENT, "call.started", {"channel": channel, "from": "+34600"})
    return call


async def test_a_written_call_this_process_runs_is_left_to_end_itself(
    reaper: Reaper, store: MemoryStore, logs: Logs, live: Live
) -> None:
    call = await a_written_call(store, "CA_chat_running", "web")
    live.serve(
        call,
        THE_AGENT,
        A_RECORD.org,
        logs.writing(call, THE_AGENT),
        None,
        context=a_call_on(call, A_RECORD.org),
        config=AgentConfig(slug=THE_AGENT),
    )
    assert await reaper.a_pass(AN_HOUR_LATER) == []


async def test_a_chat_nobody_came_back_to_after_a_restart_ends_when_the_client_gave_up(
    reaper: Reaper, store: MemoryStore
) -> None:
    call = await a_written_call(store, "CA_chat_left", "web")
    assert await reaper.a_pass(AN_HOUR_LATER) == [call]
    ended = [e.data for e in await store.since(call) if e.type == "call.ended"]
    assert (ended[0]["reason"], ended[0]["ended_by"]) == ("timeout", "platform")


async def test_a_whatsapp_thread_nobody_runs_waits_its_two_hours_for_the_contact(
    reaper: Reaper, store: MemoryStore
) -> None:
    call = await a_written_call(store, "CA_thread_left", "whatsapp")
    assert await reaper.a_pass(AN_HOUR_LATER) == [], "the contact may still write"
    assert await reaper.a_pass(AN_HOUR_LATER + 2 * 60 * 60) == [call]


async def test_a_web_call_that_rang_and_never_started_is_sealed_once_its_room_is_gone(
    reaper: Reaper, store: MemoryStore
) -> None:
    """No job came up for it, so no room opened and it is not spoken — and it is no chat either,
    because a chat writes call.started as it opens. It used to ring on the console for ever."""
    call = "CA_rang_on_the_web"
    await store.owned(call, THE_AGENT, A_RECORD.org, "production", "")
    await store.append(
        call, THE_AGENT, "call.ringing", {"channel": "web", "from": "web_1", "to": ""}
    )
    assert await reaper.a_pass(AN_HOUR_LATER) == [call]
    assert [entry.type for entry in await store.since(call)][-3:] == [
        "call.ended",
        "call.summary",
        "call.score",
    ]


async def test_a_worker_that_wrote_call_ended_and_died_is_finished_from_there(
    reaper: Reaper, store: MemoryStore
) -> None:
    """Killed mid-seal: the call.ended it managed is kept, and never written a second time."""
    call = await a_call(store, "CA_half_sealed", ended=True)
    assert await reaper.a_pass(AN_HOUR_LATER) == [call]
    written = [entry.type for entry in await store.since(call)]
    assert written.count("call.ended") == 1
    assert written[-2:] == ["call.summary", "call.score"]
    ended = next(entry for entry in await store.since(call) if entry.type == "call.ended")
    assert ended.data["reason"] == "caller_hung_up", "what the worker said stands"


async def test_a_pass_is_idempotent_and_a_second_gateway_seals_nothing_twice(
    reaper: Reaper, store: MemoryStore, logs: Logs, rooms: MemoryRooms
) -> None:
    call = await a_call(store, "CA_raced")
    other = Reaper(store, Logs(store), rooms, Live())
    first, second = await asyncio.gather(
        reaper.a_pass(AN_HOUR_LATER), other.a_pass(AN_HOUR_LATER), return_exceptions=False
    )
    assert sorted(first + second) == [call], "one of them sealed it and the other wrote nothing"
    assert [entry.type for entry in await store.since(call)].count("call.ended") == 1
    assert await reaper.a_pass(AN_HOUR_LATER) == []
    assert logs.opened(call) is None, "the process forgot it"


async def test_the_loop_runs_a_pass_before_it_ever_waits(
    reaper: Reaper, store: MemoryStore
) -> None:
    """A gateway coming up after the deploy that killed the workers ends what it left behind."""
    call = await a_call(store, "CA_at_startup")
    loop = asyncio.ensure_future(reap_forever(reaper, every=3600.0))
    while (await store.since(call))[-1].type != "call.score":  # noqa: ASYNC110 — a store, polled
        await asyncio.sleep(0)
    loop.cancel()
    assert await store.unsealed_spoken(AN_HOUR_LATER, 10) == []


async def test_the_console_stops_calling_it_live(
    gateway: TestClient, store: MemoryStore, logs: Logs, rooms: MemoryRooms
) -> None:
    """What an operator sees: the call index and the day's `live` both move with the seal."""
    call = await a_call(store, "CA_on_the_floor")
    assert got(gateway, f"/v1/insights?day={THE_DAY.isoformat()}")[1]["live"] == 1
    assert (await store.facts_of([call]))[call].ended_at is None

    assert await Reaper(store, logs, rooms, Live()).a_pass(AN_HOUR_LATER) == [call]

    _, body = got(gateway, f"/v1/insights?day={THE_DAY.isoformat()}")
    assert body["live"] == 0 and body["sessions_total"] == 1
    facts = (await store.facts_of([call]))[call]
    assert (facts.ended_at, facts.end_reason) == (
        (await store.since(call))[-3].data["ended_at"],
        "drained",
    )

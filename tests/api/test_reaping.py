"""The reaper: a spoken call whose room the SFU has lost is ended here, and nothing else is."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest
from starlette.testclient import TestClient

from pinecall.api._live import Live
from pinecall.api.reaping import NOT_JUDGED, QUIET_S, Reaper, reaping
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.routes.rooms import MemoryRooms
from tests.api.conftest import A_RECORD
from tests.api.talking import got

pytestmark = pytest.mark.unit

THE_AGENT = "clinica-norte"
THE_DAY = date(2026, 9, 17)
# The clock every call of this module is written on, and the moment a pass is run at: an hour
# later, which is longer than QUIET_S by far.
NOON = datetime(THE_DAY.year, THE_DAY.month, THE_DAY.day, 12, tzinfo=UTC).timestamp()
AN_HOUR_LATER = NOON + 60 * 60


class Clock:
    """A clock a test sets, so a call's entries land where it wants them."""

    def __init__(self) -> None:
        self.now = NOON

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


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
def reaper(store: MemoryStore, logs: Logs, rooms: MemoryRooms) -> Reaper:
    return Reaper(store, logs, rooms, Live())


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


async def test_a_call_whose_room_the_sfu_still_has_is_left_alone(
    reaper: Reaper, store: MemoryStore, rooms: MemoryRooms
) -> None:
    """A caller can be silent for an hour. While they are, the room is there and this is not it."""
    call = await a_call(store, "CA_quiet_but_live")
    rooms.open.add(call)
    written = [entry.type for entry in await store.since(call)]
    assert await reaper.a_pass(AN_HOUR_LATER) == []
    assert [entry.type for entry in await store.since(call)] == written, "not a word was added"
    assert [one.call for one in await store.unsealed_spoken(AN_HOUR_LATER, 10)] == [call]


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


async def test_a_written_call_is_not_the_reapers_business(
    reaper: Reaper, store: MemoryStore
) -> None:
    """A chat or a WhatsApp thread has no room and idles out where it runs. It is never spoken."""
    call = "CA_written"
    await store.owned(call, THE_AGENT, A_RECORD.org, "production", "")
    await store.append(call, THE_AGENT, "call.started", {"channel": "whatsapp", "from": "+34600"})
    assert await reaper.a_pass(AN_HOUR_LATER) == []
    assert not (await store.since(call))[-1].type.startswith("call.end")


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
    loop = asyncio.ensure_future(reaping(reaper, every=3600.0))
    while not (await store.since(call))[-1].type == "call.score":
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

"""Tests for a process's logs: append, live readers, replay, feeds, a call's first entries."""

import asyncio
import time
from datetime import date

import pytest

from pinecall.domain.call import CallContext, Route
from pinecall.domain.errors import Conflict, DeclarationRefused
from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log.logs import QUEUE_DEPTH, Fanout, Log, Logs, arrival_entry, started_entry
from pinecall.log.readers import Filter, parse_filter
from pinecall.log.store import Claim, Claimant, Store
from pinecall.wire.frames import Entry
from pinecall.wire.rest.calls import BatchedEntry
from tests.conftest import postgres

AGENT = "dental-sur"
A_SCORE: JsonObject = {"passed": True, "judges": [], "judge_calls": 0}
RINGING: JsonObject = {
    "channel": "phone",
    "from": "+34600",
    "to": "+34900",
    "route": {"channel": "phone", "number": "+34900"},
    "caller": None,
}

# Fills a queue many times over; the bound guards against blocking, it is not a benchmark.
A_BURST = 5_000
THE_BURST_MUST_TAKE_UNDER_S = 1.0


def note(n: int) -> JsonObject:
    return {"name": "note", "data": {"n": n}}


def entry_of(seq: int, kind: str = "custom") -> Entry:
    return Entry(
        seq=seq, ts=float(seq), call="CA_1", agent=AGENT, type=kind, ephemeral=False, data={}
    )


# ── the fanout ──


async def test_every_reader_gets_every_entry_in_the_order_it_was_written() -> None:
    fanout = Fanout()
    first, second = fanout.subscribe(), fanout.subscribe()
    for seq in range(1, 4):
        fanout.publish(entry_of(seq))
    fanout.close()
    assert [item.seq async for item in first] == [1, 2, 3]
    assert [item.seq async for item in second] == [1, 2, 3]


async def test_a_readers_filter_narrows_what_reaches_its_queue() -> None:
    fanout = Fanout()
    reader = fanout.subscribe(parse_filter("turn.user", durable=False))
    for seq, kind in enumerate(("turn.user", "metrics.llm", "call.ended"), 1):
        fanout.publish(entry_of(seq, kind))
    fanout.close()
    assert [item.type async for item in reader] == ["turn.user", "call.ended"]


async def test_a_slow_reader_is_dropped_and_the_burst_never_waits_for_it() -> None:
    fanout = Fanout()
    slow = fanout.subscribe()
    started = time.perf_counter()
    for seq in range(1, A_BURST + 1):
        fanout.publish(entry_of(seq))
    assert time.perf_counter() - started < THE_BURST_MUST_TAKE_UNDER_S
    assert slow.dropped
    assert fanout.readers == 0


async def test_a_dropped_reader_still_reads_what_it_was_given_and_then_ends() -> None:
    fanout = Fanout()
    slow = fanout.subscribe()
    for seq in range(1, QUEUE_DEPTH + 51):
        fanout.publish(entry_of(seq))
    assert [item.seq async for item in slow] == list(range(1, QUEUE_DEPTH + 1))


async def test_a_fast_reader_keeps_up_while_a_slow_one_is_dropped() -> None:
    fanout = Fanout()
    slow, fast = fanout.subscribe(), fanout.subscribe()
    for seq in range(1, QUEUE_DEPTH + 2):
        fanout.publish(entry_of(seq))
        assert (await anext(fast)).seq == seq
    assert (slow.dropped, fast.dropped, fanout.readers) == (True, False, 1)


async def test_closing_the_log_ends_every_iteration_and_a_late_reader_gets_nothing() -> None:
    fanout = Fanout()
    early = fanout.subscribe()
    fanout.publish(entry_of(1))
    fanout.close()
    assert [item.seq async for item in early] == [1]
    assert [item.seq async for item in fanout.subscribe()] == []


# ── one log ──


@postgres
async def test_an_append_carries_the_call_and_the_agent_into_the_store(
    store: Store, call: str
) -> None:
    written = await Log(store, call, AGENT).append("call.started", {"channel": "phone"})
    assert (written.call, written.agent, written.seq) == (call, AGENT, 1)
    assert await store.since(call) == [written]


@postgres
async def test_the_wire_decides_what_is_ephemeral_and_the_caller_may_overrule_it(
    store: Store, call: str
) -> None:
    log = Log(store, call, AGENT)
    assert (await log.append("user.transcript", {"text": "ho"})).ephemeral
    assert not (await log.append("turn.user", {"text": "hola"})).ephemeral
    assert not (await log.append("user.transcript", {"text": "ho"}, ephemeral=False)).ephemeral


@postgres
async def test_every_live_reader_hears_the_entry_the_store_already_has(
    store: Store, call: str
) -> None:
    log = Log(store, call, AGENT)
    reader = log.fanout.subscribe()
    written = await log.append("custom", note(1))
    assert (await anext(reader)).seq == written.seq
    assert await store.latest_seq(call) == 1


@postgres
async def test_a_first_entry_reaches_the_readers_once_and_an_agents_log_takes_none(
    store: Store, call: str
) -> None:
    log = Log(store, call, AGENT)
    await log.append("call.started", {})
    reader = log.fanout.subscribe()
    written = await log.append_first("call.ended", {"reason": "drained"})
    assert written is not None
    assert await log.append_first("call.ended", {"reason": "drained"}) is None
    assert (await anext(reader)).seq == written.seq
    with pytest.raises(DeclarationRefused, match="only a call's log"):
        await Log(store, None, AGENT).append_first("call.ended", {})


@postgres
async def test_a_tap_runs_on_every_append_before_append_returns(store: Store, call: str) -> None:
    heard: list[str] = []

    async def tap(item: Entry) -> None:
        heard.append(item.type)

    log = Log(store, call, AGENT)
    log.tapped(tap)
    await log.append("custom", note(1))
    assert heard == ["custom"]


@postgres
async def test_a_reader_hears_a_batch_once_in_order_and_its_retry_not_at_all(
    store: Store, call: str
) -> None:
    heard: list[int] = []

    async def tap(item: Entry) -> None:
        heard.append(item.seq)

    log = Log(store, call, AGENT)
    log.tapped(tap)
    reader = log.fanout.subscribe()
    batch = [
        BatchedEntry(type="custom", data=note(1), ts=0.5),
        BatchedEntry(type="user.transcript", data={"text": "ho"}, ts=0.5),
        BatchedEntry(type="custom", data=note(2), ts=0.5),
    ]
    written = await log.append_many(batch, after=0)
    again = await log.append_many(batch, after=0)
    await log.append("custom", note(3))
    log.fanout.close()
    assert [entry.seq for entry in written] == [entry.seq for entry in again] == [1, 2, 3]
    assert [entry.ephemeral for entry in written] == [False, True, False]
    assert [item.seq async for item in reader] == [1, 2, 3, 4]
    assert heard == [1, 2, 3, 4]


@postgres
async def test_the_terminal_entry_seals_the_log(store: Store, call: str) -> None:
    log = Log(store, call, AGENT)
    await log.append("call.summary", {"x": 1})
    assert not log.sealed, "the verdict on a call comes after its summary"
    await log.append("call.score", A_SCORE)
    assert log.sealed
    assert await store.sealed(call)
    with pytest.raises(Conflict, match="has ended"):
        await log.append("custom", note(9))


@postgres
async def test_sealing_closes_the_live_readers_too(store: Store, call: str) -> None:
    log = Log(store, call, AGENT)
    reader = log.fanout.subscribe()
    await log.append("call.summary", {"x": 1})
    await log.append("call.score", A_SCORE)
    assert [item.type async for item in reader] == ["call.summary", "call.score"]


@postgres
async def test_the_agents_own_log_writes_outside_every_call_and_never_seals(store: Store) -> None:
    Log(store, None, AGENT)
    log = Log(store, None, AGENT)
    registered = await log.append("agent.registered", {"routes": []})
    assert (registered.call, registered.seq) == (None, 1)
    with pytest.raises(DeclarationRefused, match="has no end"):
        await log.seal()
    await log.append("call.score", A_SCORE)
    assert not log.sealed


@postgres
async def test_a_snapshot_is_folded_again_only_when_the_log_moved(store: Store, call: str) -> None:
    log = Log(store, call, AGENT)
    await log.append("custom", note(1))
    first = await log.snapshot()
    assert await log.snapshot() is first
    await log.append("user.transcript", {"text": "ho", "final": False})
    moved = await log.snapshot()
    assert moved is not first, "an ephemeral moves the log too"
    assert len(moved.custom) == 1


# ── replay ──


@postgres
async def test_the_backlog_comes_first_then_the_marker_then_the_live_half(
    store: Store, call: str
) -> None:
    log = Log(store, call, AGENT)
    await log.append("custom", note(1))
    await log.append("custom", note(2))
    stream = log.stream()
    assert [(await anext(stream)).seq for _ in range(2)] == [1, 2]
    marker = await anext(stream)
    assert (marker.type, marker.seq, marker.ephemeral) == ("log.caught_up", 2, True)
    await log.append("custom", note(3))
    assert (await anext(stream)).seq == 3


@postgres
async def test_an_empty_log_says_caught_up_at_zero(store: Store, call: str) -> None:
    marker = await anext(Log(store, call, AGENT).stream())
    assert (marker.type, marker.seq, marker.data) == ("log.caught_up", 0, {"seq": 0})


@postgres
async def test_the_cursor_is_the_whole_protocol(store: Store, call: str) -> None:
    log = Log(store, call, AGENT)
    for n in range(1, 6):
        await log.append("custom", note(n))
    stream = log.stream(after=3)
    assert [(await anext(stream)).seq for _ in range(2)] == [4, 5]
    assert (await anext(stream)).type == "log.caught_up"


@postgres
async def test_a_filter_narrows_the_backlog_and_the_live_half_alike(
    store: Store, call: str
) -> None:
    log = Log(store, call, AGENT)
    await log.append("custom", note(1))
    await log.append("state.changed", {"state": {}, "changed": []})
    stream = log.stream(only=Filter(types=frozenset({"custom"})))
    assert (await anext(stream)).type == "custom"
    assert (await anext(stream)).type == "log.caught_up"
    await log.append("state.changed", {"state": {}, "changed": []})
    await log.append("custom", note(4))
    assert (await anext(stream)).seq == 4


@postgres
async def test_a_reader_that_falls_behind_gets_a_gap_with_the_state_in_it(
    store: Store, call: str
) -> None:
    log = Log(store, call, AGENT)
    await log.append("custom", note(1))
    stream = log.stream()
    assert (await anext(stream)).seq == 1
    assert (await anext(stream)).type == "log.caught_up"
    behind = QUEUE_DEPTH + 44
    for n in range(2, 2 + behind):
        await log.append("custom", note(n))
    given = [(await anext(stream)).seq for _ in range(QUEUE_DEPTH)]
    assert given == list(range(2, 2 + QUEUE_DEPTH)), "what it was given is whole and in order"
    gap = await anext(stream)
    last = 1 + behind
    assert (gap.type, gap.ephemeral, gap.seq) == ("log.gap", True, last)
    assert (gap.data["from_seq"], gap.data["to_seq"]) == (2 + QUEUE_DEPTH, last)
    snapshot = gap.data["snapshot"]
    assert isinstance(snapshot, dict)
    assert snapshot["seq"] == last
    assert isinstance(snapshot["custom"], list)
    assert len(snapshot["custom"]) == last
    assert ((await anext(stream)).type, log.fanout.readers) == ("log.caught_up", 1)


@postgres
async def test_the_stream_ends_when_the_call_does(store: Store, call: str) -> None:
    log = Log(store, call, AGENT)
    stream = log.stream()
    assert (await anext(stream)).type == "log.caught_up"
    await log.append("call.score", A_SCORE)
    assert (await anext(stream)).type == "call.score"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


# v1 read "sealed" as "the last entry is call.score", so a log sealed without one kept its
# readers waiting forever after log.caught_up.
@postgres
async def test_a_log_sealed_without_a_verdict_ends_its_stream_after_the_backlog(
    store: Store, call: str
) -> None:
    await Log(store, call, AGENT).append("call.ended", {"reason": "no_answer"})
    await store.seal(call)
    later = Log(store, call, AGENT)
    kinds = [item.type async for item in later.stream()]
    assert kinds == ["call.ended", "log.caught_up"]


@postgres
async def test_an_agents_stream_that_falls_behind_is_told_and_reads_the_store_again(
    store: Store,
) -> None:
    Log(store, None, AGENT)
    log = Log(store, None, AGENT)
    stream = log.stream()
    assert (await anext(stream)).type == "log.caught_up"
    for n in range(QUEUE_DEPTH + 10):
        await log.append("custom", note(n))
    for _ in range(QUEUE_DEPTH):
        await anext(stream)
    gap = await anext(stream)
    assert (gap.type, gap.data["snapshot"], gap.data["from_seq"]) == (
        "log.gap",
        None,
        QUEUE_DEPTH + 1,
    )
    assert [(await anext(stream)).seq for _ in range(10)] == list(
        range(QUEUE_DEPTH + 1, QUEUE_DEPTH + 11)
    )


# ── the process's logs and feeds ──


@postgres
async def test_a_reader_and_the_writer_of_a_call_share_one_log(store: Store, call: str) -> None:
    logs = Logs(store)
    reader = logs.reading(call).fanout.subscribe()
    await logs.writing(call, AGENT).append("custom", note(1))
    assert (await asyncio.wait_for(anext(reader), 1)).seq == 1
    assert logs.opened(call) is logs.writing(call, AGENT)
    assert logs.opened(f"{call}-other") is None


@postgres
async def test_every_orgs_floor_reaches_the_box_and_a_turn_does_not(
    store: Store, call: str
) -> None:
    logs = Logs(store)
    box = logs.box.subscribe()
    other = f"{call}-b"
    await store.claim(call, AGENT, "org_a")
    await store.claim(other, "tienda", "org_b")
    await logs.writing(call, AGENT).append("call.ringing", RINGING)
    await logs.writing(call, AGENT).append("turn.user", {"text": "hola", "speech_id": "u1"})
    await logs.writing(other, "tienda").append("call.ringing", RINGING)
    heard = [await asyncio.wait_for(anext(box), 1) for _ in range(2)]
    assert [(item.call, item.type) for item in heard] == [
        (call, "call.ringing"),
        (other, "call.ringing"),
    ]


@postgres
async def test_a_log_nobody_owns_never_reaches_the_box(store: Store, call: str) -> None:
    logs = Logs(store)
    box = logs.box.subscribe()
    await logs.writing(f"{call}-nobodys", AGENT).append("call.ringing", RINGING)
    await store.claim(call, AGENT, "org_a")
    await logs.writing(call, AGENT).append("call.ringing", RINGING)
    assert (await asyncio.wait_for(anext(box), 1)).call == call


@postgres
async def test_the_orgs_own_feed_still_hears_its_own_while_the_box_listens(
    store: Store, call: str
) -> None:
    logs = Logs(store)
    box, feed = logs.box.subscribe(), logs.feed("org_a", "production").subscribe()
    elsewhere = logs.feed("org_b", "production").subscribe()
    await store.claim(call, AGENT, "org_a")
    await logs.writing(call, AGENT).append("call.ringing", RINGING)
    assert (await asyncio.wait_for(anext(feed), 1)).call == call
    assert (await asyncio.wait_for(anext(box), 1)).call == call
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(anext(elsewhere), 0.1)


@postgres
async def test_an_orgs_feed_is_one_worlds_and_its_agents_own_entries_reach_both(
    store: Store, call: str
) -> None:
    logs = Logs(store)
    production = logs.feed("org_a", "production").subscribe()
    sandbox = logs.feed("org_a", "sandbox").subscribe()
    await store.claim(call, AGENT, "org_a", Claim(Scope("org_a", "sandbox")))
    await store.claim(None, AGENT, "org_a")
    await logs.writing(call, AGENT).append("call.ringing", RINGING)
    await logs.agent(AGENT).append("agent.registered", {"routes": []})
    assert (await asyncio.wait_for(anext(sandbox), 1)).type == "call.ringing"
    assert (await asyncio.wait_for(anext(production), 1)).type == "agent.registered"
    assert (await asyncio.wait_for(anext(sandbox), 1)).type == "agent.registered"
    assert await logs.claimant_of(
        Entry(seq=1, ts=1.0, call=call, agent=AGENT, type="x", ephemeral=False, data={})
    ) == Claimant("org_a", "sandbox")


@postgres
async def test_whose_a_log_is_is_asked_of_the_store_once_and_not_per_entry(
    store: Store, call: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    params: list[str | None] = []
    claimant = store.claimant

    async def counted(of: str | None, agent: str) -> Claimant | None:
        params.append(of)
        return await claimant(of, agent)

    monkeypatch.setattr(store, "claimant", counted)
    logs = Logs(store)
    await store.claim(call, AGENT, "org_1")
    for _ in range(3):
        await logs.writing(call, AGENT).append("call.started", {})
    assert params == [call]
    logs.forget(call)
    await logs.writing(call, AGENT).append("call.ringing", RINGING)
    assert params == [call, call], "forgotten with the call, asked again for the next"


@postgres
async def test_a_log_only_read_is_let_go_once_nobody_reads_it(store: Store, call: str) -> None:
    logs = Logs(store)
    first = logs.reading(call)
    assert logs.reading(call) is first, "asked again before anyone subscribed, it is the same log"
    logs.reading(f"{call}-other")
    assert logs.reading(call) is not first, "a log nobody reads or writes is let go"
    kept = logs.reading(call)
    reader = kept.fanout.subscribe()
    logs.reading(f"{call}-other")
    assert logs.reading(call) is kept, "a log somebody reads stays"
    reader.close()


# ── the first entries of a call ──

ROUTE = Route(org="clinica", agent=AGENT, channel="phone", number="+34955111222")


def context(direction: str) -> CallContext:
    return CallContext(
        call="CA_1",
        channel="phone",
        direction="outbound" if direction == "out" else "inbound",
        caller="+34611222333",
        route=ROUTE,
        today=date(2026, 9, 27),
        persona="homeowner",
        accepts_when="a slot on friday",
    )


def test_an_offered_call_rings_from_the_caller_to_the_door_through_its_route() -> None:
    kind, data = arrival_entry(context("in"), "+34955111222")
    assert kind == "call.ringing"
    assert (data["from"], data["to"], data["route"]) == (
        "+34611222333",
        "+34955111222",
        {"channel": "phone", "number": "+34955111222"},
    )


def test_a_placed_call_dials_from_the_door_and_says_who_placed_it() -> None:
    kind, data = arrival_entry(context("out"), "+34955111222", asked_by="m_berna")
    assert kind == "call.dialing"
    assert (data["from"], data["to"], data["asked_by"]) == (
        "+34955111222",
        "+34611222333",
        "m_berna",
    )


def test_the_start_carries_the_persona_and_its_rules_for_the_judges() -> None:
    started = started_entry(context("in"), "+34955111222", at=12.5)
    assert (started["started_at"], started["persona"], started["accepts_when"], started["env"]) == (
        12.5,
        "homeowner",
        "a slot on friday",
        "production",
    )


@postgres
async def test_the_readers_held_are_every_log_and_feed_subscriber(store: Store, call: str) -> None:
    logs = Logs(store)
    assert logs.readers == 0
    call_reader = logs.reading(call).fanout.subscribe()
    logs.feed("org_1", "production").subscribe()
    logs.box.subscribe()
    assert logs.readers == 3
    call_reader.close()
    assert logs.readers == 2

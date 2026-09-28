"""A widget's channel: the public log of its own call, and the events it may send."""

import asyncio
import json
from collections.abc import AsyncIterator

from livekit import rtc

from pinecall.domain.agent import AgentConfig, Visibility
from pinecall.log.logs import Log
from pinecall.log.store import Store
from pinecall.session.call import Call
from pinecall.session.widget import EVENT, LOG, REPLAY, SNAPSHOT, Reading, Widget
from pinecall.wire.frames import Entry
from pinecall.wire.state import State
from tests.conftest import postgres
from tests.fakes.acme import seat
from tests.fakes.livekit import Room
from tests.session.conftest import Box, context_of

WIDGET = {"pinecall.scope": "talk"}
RELIABLE = rtc.DataPacketKind.KIND_RELIABLE
AGENT = AgentConfig(
    slug="clinica-norte",
    events={"clicked": frozenset({"participant"})},
    state_fields={"card": "pii"},
)


def _reading(log: Log, store: Store) -> Reading:
    async def state() -> tuple[State, int]:
        assert log.call is not None
        return await log.snapshot(), await store.latest_seq(log.call)

    async def since(after: int) -> AsyncIterator[Entry]:
        assert log.call is not None
        for entry in await store.whole(log.call, after=after):
            yield entry

    async def tail(after: int) -> AsyncIterator[Entry]:
        async for entry in log.stream(after=after):
            if entry.type != "log.caught_up":
                yield entry

    return Reading(state=state, since=since, tail=tail)


def _widget(
    box: Box, store: Store, visibility: dict[str, Visibility] | None = None
) -> tuple[Widget, Room]:
    assert box.log.call is not None
    config = AgentConfig(
        slug=AGENT.slug, events=AGENT.events, state_fields=visibility or AGENT.state_fields
    )
    call = Call(context_of(box.log.call, "web"), config, box.platform())
    call.writing.open()
    offline = Room(box.log.call)
    widget = Widget(call, offline, _reading(box.log, store))
    widget.watch()
    return widget, offline


def _sent(offline: Room, topic: str) -> list[dict[str, object]]:
    return [json.loads(packed) for sent, packed, _ in offline.me.published if sent == topic]


async def _settled() -> None:
    await asyncio.sleep(0.1)


@postgres
async def test_a_widget_gets_the_snapshot_first_and_then_the_log_as_it_grows(
    box: Box, store: Store
) -> None:
    await box.log.append("state.changed", {"state": {"step": 1}, "changed": ["step"]})
    widget, offline = _widget(box, store)
    offline.emit("participant_connected", seat("visitor_1", attributes=WIDGET))
    await _settled()
    await box.log.append("state.changed", {"state": {"step": 2}, "changed": ["step"]})
    await _settled()
    widget.stop()
    (snapshot,) = _sent(offline, SNAPSHOT)
    assert snapshot["last_seq"] == 1
    assert [entry["seq"] for entry in _sent(offline, LOG)] == [2]


@postgres
async def test_nothing_of_the_tenants_leaves_for_the_browser(box: Box, store: Store) -> None:
    await box.log.append("state.changed", {"state": {"card": "4111"}, "changed": ["card"]})
    widget, offline = _widget(box, store)
    offline.emit("participant_connected", seat("visitor_1", attributes=WIDGET))
    await _settled()
    widget.stop()
    assert "4111" not in json.dumps(_sent(offline, SNAPSHOT))


@postgres
async def test_a_replay_resends_from_the_cursor_and_ends_with_caught_up(
    box: Box, store: Store
) -> None:
    for step in (1, 2, 3):
        await box.log.append("state.changed", {"state": {"step": step}, "changed": ["step"]})
    widget, offline = _widget(box, store)
    visitor = seat("visitor_1", attributes=WIDGET)
    offline.emit("participant_connected", visitor)
    await _settled()
    offline.emit(
        "data_received",
        rtc.DataPacket(data=b'{"after": 1}', kind=RELIABLE, participant=visitor, topic=REPLAY),
    )
    await _settled()
    widget.stop()
    replayed = [(entry["type"], entry["seq"]) for entry in _sent(offline, LOG)]
    assert replayed == [("state.changed", 2), ("state.changed", 3), ("log.caught_up", 3)]


@postgres
async def test_a_declared_event_from_a_widget_lands_as_event_received_with_its_identity(
    box: Box, store: Store, call: str
) -> None:
    widget, offline = _widget(box, store)
    visitor = seat("visitor_1", attributes=WIDGET)
    packet = json.dumps({"name": "clicked", "data": {"x": 1}}).encode()
    offline.emit(
        "data_received",
        rtc.DataPacket(data=packet, kind=RELIABLE, participant=visitor, topic=EVENT),
    )
    await widget.call.writing.flushed(5)
    widget.stop()
    (received,) = await store.whole(call)
    assert (received.type, received.data["identity"], received.data["source"]) == (
        "event.received",
        "visitor_1",
        "participant",
    )


@postgres
async def test_an_undeclared_or_malformed_event_never_reaches_the_log(
    box: Box, store: Store, call: str
) -> None:
    widget, offline = _widget(box, store)
    visitor = seat("visitor_1", attributes=WIDGET)
    for packet in (b'{"name": "paid", "data": {}}', b'{"name": "clicked"}', b"not json"):
        offline.emit(
            "data_received",
            rtc.DataPacket(data=packet, kind=RELIABLE, participant=visitor, topic=EVENT),
        )
    await widget.call.writing.flushed(5)
    widget.stop()
    assert await store.whole(call) == []


@postgres
async def test_only_a_widgets_seat_speaks_on_the_channel(box: Box, store: Store, call: str) -> None:
    widget, offline = _widget(box, store)
    stranger = seat("ana", attributes={"pinecall.scope": "supervise"})
    packet = json.dumps({"name": "clicked", "data": {}}).encode()
    offline.emit(
        "data_received",
        rtc.DataPacket(data=packet, kind=RELIABLE, participant=stranger, topic=EVENT),
    )
    offline.emit("participant_connected", stranger)
    await _settled()
    widget.stop()
    assert await store.whole(call) == []
    assert offline.me.published == []


@postgres
async def test_a_widget_that_left_is_sent_nothing_more(box: Box, store: Store) -> None:
    widget, offline = _widget(box, store)
    visitor = seat("visitor_1", attributes=WIDGET)
    offline.emit("participant_connected", visitor)
    await _settled()
    offline.emit("participant_disconnected", visitor)
    await box.log.append("state.changed", {"state": {"step": 2}, "changed": ["step"]})
    await _settled()
    widget.stop()
    assert _sent(offline, LOG) == []


@postgres
async def test_stopping_cancels_the_tail_and_lets_go_of_the_room(box: Box, store: Store) -> None:
    widget, offline = _widget(box, store)
    offline.emit("participant_connected", seat("visitor_1", attributes=WIDGET))
    await _settled()
    tailing = widget.tailing
    widget.stop()
    await asyncio.sleep(0)
    assert tailing is not None
    assert tailing.cancelled() or tailing.done()
    offline.emit("participant_connected", seat("visitor_2", attributes=WIDGET))
    await _settled()
    assert len(_sent(offline, SNAPSHOT)) == 1


@postgres
async def test_a_widget_joining_late_is_caught_up_and_then_nothing_arrives_twice(
    box: Box, store: Store
) -> None:
    widget, offline = _widget(box, store)
    first = seat("visitor_1", attributes=WIDGET)
    offline.emit("participant_connected", first)
    await _settled()
    await box.log.append("state.changed", {"state": {"step": 1}, "changed": ["step"]})
    await _settled()
    late = seat("visitor_2", attributes=WIDGET)
    offline.emit("participant_connected", late)
    await _settled()
    await box.log.append("state.changed", {"state": {"step": 2}, "changed": ["step"]})
    await _settled()
    widget.stop()
    to_the_late = [
        json.loads(packed)["seq"]
        for topic, packed, to in offline.me.published
        if topic == LOG and to == ["visitor_2"]
    ]
    assert to_the_late == sorted(set(to_the_late))
    assert to_the_late[-1] == 2

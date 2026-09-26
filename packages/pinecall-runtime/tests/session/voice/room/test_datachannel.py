"""The DataChannel: the snapshot, then the log projected public; a replay; what a widget sends."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from pinecall.log.entry import Entry
from pinecall.log.projection import PUBLIC_STATE_FIELDS
from pinecall.log.reduce import reduce
from pinecall.session.voice.room import DataChannel
from pinecall.session.voice.room.datachannel import EVENT, LOG, REPLAY, SNAPSHOT
from pinecall.types import AgentConfig
from pinecall.types.json import JsonObject
from pinecall.types.scopes import SCOPE_ATTRIBUTE
from pinecall_protocol import encode
from tests.session.voice.fakes import CALL, CLARA
from tests.session.voice.room.fakes import FakeLog, FakeParticipant, Held, a_held_room, a_widget

pytestmark = pytest.mark.unit

WIDGET = "web_ab12cd34ef56"

# The agent declares what a browser may hand it; the widget's form is in, the app's slot is not.
LISTENING = replace(
    CLARA,
    events={"form.submitted": frozenset({"participant"}), "slot.released": frozenset({"app"})},
)


def an_entry(seq: int, type: str, data: JsonObject, ephemeral: bool = False) -> JsonObject:
    return {
        "seq": seq,
        "ts": 1_700_000_000.0 + seq,
        "call": CALL,
        "agent": "clinica-norte",
        "type": type,
        "ephemeral": ephemeral,
        "data": data,
    }


# A log with the tenant's fields all over it: a number, a tool, a turn's costs.
ENTRIES: list[JsonObject] = [
    an_entry(
        1,
        "call.started",
        {
            "channel": "web",
            "direction": "inbound",
            "from": "+59897777",
            "to": "clinica-norte",
            "caller": None,
            "started_at": 1_700_000_000.0,
        },
    ),
    an_entry(2, "tool.call", {"call_id": "t1", "name": "find_slot", "arguments": {"day": "lunes"}}),
    an_entry(
        3,
        "turn.agent",
        {
            "speech_id": "s1",
            "text": "Hay un turno a las 10:15.",
            "interrupted": False,
            "metrics": {"e2e_latency": 0.9, "llm_node_ttft": 0.4, "tts_node_ttfb": 0.2},
        },
    ),
]


def a_log(entries: list[JsonObject] = ENTRIES) -> FakeLog:
    """The gateway's view of this log: the state it folds to, and the entries themselves."""
    state = reduce(Entry.model_validate(one) for one in entries)
    return FakeLog(encode(state), entries)


@pytest.fixture
async def held() -> Held:
    return a_held_room()


def channel(held: Held, log: FakeLog, config: AgentConfig = CLARA) -> DataChannel:
    """A DataChannel over this room and this log, watching before the room connects."""
    built = DataChannel(held.holding, log, config, CALL)
    built.watch(held.holding.room)
    return built


async def test_a_widget_gets_the_snapshot_first_and_then_the_log_as_it_grows(held: Held) -> None:
    log = a_log()
    channel(held, log)
    held.room.connect()
    held.room.join(a_widget(WIDGET))
    await held.settled()

    (snapshot,) = held.room.sent
    assert (snapshot.topic, snapshot.to) == (SNAPSHOT, [WIDGET])
    assert snapshot.payload["last_seq"] == 3
    assert set(snapshot.payload["state"]) <= set(PUBLIC_STATE_FIELDS)

    log.push(an_entry(4, "turn.user", {"speech_id": "s2", "text": "Perfecto", "metrics": {}}))
    log.push(an_entry(5, "tool.result", {"call_id": "t2", "name": "book", "ok": True}))
    await held.settled()
    assert [packet.topic for packet in held.room.sent[1:]] == [LOG]
    assert held.room.sent[1].payload["seq"] == 4
    assert held.room.sent[1].payload["type"] == "turn.user"


async def test_nothing_of_the_tenants_leaves_for_the_browser(held: Held) -> None:
    log = a_log()
    channel(held, log)
    held.room.connect()
    held.room.join(a_widget(WIDGET))
    await held.settled()
    log.push(an_entry(4, "prompt.changed", {"name": "view", "hash": "abc", "chars": 12}))
    log.push(
        an_entry(
            5,
            "turn.agent",
            {
                "speech_id": "s3",
                "text": "Listo.",
                "interrupted": False,
                "metrics": {"e2e_latency": 1.1, "llm_node_ttft": 0.5},
            },
        )
    )
    await held.settled()

    snapshot, *entries = held.room.sent
    assert "agent" not in snapshot.payload["state"] and "call" not in snapshot.payload["state"]
    said = str(held.room.sent)
    for tenant_word in ("+59897777", "find_slot", "llm_node_ttft", "prompt.changed", "abc"):
        assert tenant_word not in said, tenant_word
    (turn,) = entries
    assert set(turn.payload) == {"seq", "ts", "type", "ephemeral", "data"}
    assert turn.payload["data"]["metrics"] == {"e2e_latency": 1.1}


async def test_a_replay_resends_from_the_cursor_and_ends_with_caught_up(held: Held) -> None:
    log = a_log()
    channel(held, log)
    held.room.connect()
    widget = a_widget(WIDGET)
    held.room.join(widget)
    await held.settled()

    held.room.receive(widget, REPLAY, {"after": 1})
    await held.settled()
    replayed = held.room.sent[1:]
    assert [(one.topic, one.payload["seq"], one.payload["type"]) for one in replayed] == [
        (LOG, 3, "turn.agent"),
        (LOG, 3, "log.caught_up"),
    ]
    assert replayed[-1].payload["data"] == {"seq": 3}
    assert all(one.to == [WIDGET] for one in replayed)


async def test_a_widget_joining_late_is_caught_up_and_then_nothing_arrives_twice(
    held: Held,
) -> None:
    log = a_log()
    channel(held, log)
    held.room.connect()
    held.room.join(a_widget(WIDGET))
    await held.settled()
    log.push(an_entry(4, "turn.user", {"speech_id": "s2", "text": "Sí", "metrics": {}}))
    await held.settled()

    late = a_widget("web_late")
    held.room.join(late)
    await held.settled()
    log.push(an_entry(5, "turn.user", {"speech_id": "s3", "text": "Gracias", "metrics": {}}))
    await held.settled()

    to_the_late_one = [one for one in held.room.sent if one.to == ["web_late"]]
    assert [one.topic for one in to_the_late_one] == [SNAPSHOT, LOG]
    assert to_the_late_one[0].payload["last_seq"] == 4
    assert to_the_late_one[1].payload["seq"] == 5
    to_the_first = [one for one in held.room.sent if one.to == [WIDGET]]
    assert [one.payload.get("seq") for one in to_the_first[1:]] == [4, 5]


async def test_a_declared_event_from_a_widget_lands_as_event_received_with_its_identity(
    held: Held,
) -> None:
    channel(held, a_log(), LISTENING)
    held.room.connect()
    widget = a_widget(WIDGET)
    held.room.join(widget)
    await held.settled()
    held.room.receive(widget, EVENT, {"name": "form.submitted", "data": {"dni": "1234"}})
    await held.settled()
    (received,) = held.recording.of("event.received")
    assert received.data == {
        "name": "form.submitted",
        "data": {"dni": "1234"},
        "source": "participant",
        "identity": WIDGET,
    }


@pytest.mark.parametrize(
    "said",
    [
        {"name": "slot.released", "data": {}},
        {"name": "never.declared", "data": {}},
        {"name": "form.submitted"},
        "not even an object",
    ],
)
async def test_an_undeclared_or_malformed_event_never_reaches_the_log(
    held: Held, said: Any, caplog: pytest.LogCaptureFixture
) -> None:
    channel(held, a_log(), LISTENING)
    held.room.connect()
    widget = a_widget(WIDGET)
    held.room.join(widget)
    await held.settled()
    held.room.receive(widget, EVENT, said)
    await held.settled()
    assert held.recording.of("event.received") == []
    assert held.recording.of("error") == []
    assert len(caplog.records) == 1


async def test_only_a_participate_token_speaks_on_the_channel(held: Held) -> None:
    channel(held, a_log(), LISTENING)
    held.room.connect()
    supervisor = FakeParticipant("ana", attributes={SCOPE_ATTRIBUTE: "supervise"})
    held.room.join(supervisor)
    await held.settled()
    held.room.receive(supervisor, EVENT, {"name": "form.submitted", "data": {}})
    held.room.receive(supervisor, REPLAY, {"after": 0})
    await held.settled()
    assert held.recording.of("event.received") == []
    assert held.room.sent == []


async def test_a_widget_that_left_is_sent_nothing_more(held: Held) -> None:
    log = a_log()
    channel(held, log)
    held.room.connect()
    widget = a_widget(WIDGET)
    held.room.join(widget)
    await held.settled()
    held.room.leave(widget, 1)
    log.push(an_entry(4, "turn.user", {"speech_id": "s2", "text": "Sí", "metrics": {}}))
    await held.settled()
    assert [one.topic for one in held.room.sent] == [SNAPSHOT]


async def test_stopping_cancels_the_tail_and_lets_go_of_the_room(held: Held) -> None:
    log = a_log()
    built = channel(held, log)
    held.room.connect()
    held.room.join(a_widget(WIDGET))
    await held.settled()
    built.stop()
    log.push(an_entry(4, "turn.user", {"speech_id": "s2", "text": "Sí", "metrics": {}}))
    held.room.join(a_widget("web_other"))
    await held.settled()
    assert len(held.room.sent) == 1

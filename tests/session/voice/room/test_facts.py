"""The room's events as facts: who joined and as what, who spoke, who left, in the room's order."""

from __future__ import annotations

import time

import pytest
from livekit import rtc

from pinecall.log.entry import Entry
from pinecall.log.reduce import reduce
from pinecall.session.voice.room import Facts
from pinecall.types.token import SCOPE_ATTRIBUTE
from tests.session.voice.fakes import CALL, Written
from tests.session.voice.room.fakes import FakeParticipant, Held, a_caller, a_held_room, a_widget

pytestmark = pytest.mark.unit

CALLER = "+59897777"


@pytest.fixture
async def held() -> Held:
    return a_held_room()


def watching(held: Held, caller: str = CALLER) -> Facts:
    """The facts of a phone call resolved for this caller, subscribed before the room connects."""
    facts = Facts(held.holding.writing, "phone", caller)
    facts.watch(held.holding.room)
    return facts


async def test_a_caller_joining_speaking_and_leaving_is_four_facts_in_that_order(
    held: Held,
) -> None:
    watching(held)
    caller = a_caller(CALLER)
    held.room.connect()
    await held.settled()
    held.room.join(caller)
    held.room.speaking(caller)
    held.room.speaking()
    held.room.leave(caller, rtc.DisconnectReason.CLIENT_INITIATED)
    await held.settled()

    about_the_caller = [
        entry for entry in held.recording.entries if entry.data.get("identity") == caller.identity
    ]
    assert held.recording.types[0] == "room.opened"
    assert [entry.type for entry in about_the_caller] == [
        "participant.joined",
        "participant.speaking",
        "participant.speaking",
        "participant.left",
    ]
    assert [entry.data["speaking"] for entry in held.recording.of("participant.speaking")] == [
        True,
        False,
    ]
    assert held.recording.of("participant.left")[0].data["reason"] == "client_initiated"


async def test_the_sip_attributes_travel_verbatim_and_the_caller_is_read_from_them(
    held: Held,
) -> None:
    watching(held)
    caller = a_caller(CALLER)
    held.room.connect()
    await held.settled()
    held.room.join(caller)
    await held.settled()

    joined = held.recording.of("participant.joined")[-1]
    assert joined.data["kind"] == "caller"
    assert joined.data["attributes"] == caller.attributes

    state = reduce(_as_a_log(held.recording.entries))
    assert state.room is not None
    assert state.room.caller == caller.identity
    seat = next(one for one in state.room.participants if one.identity == caller.identity)
    assert seat.attributes["sip.phoneNumber"] == CALLER


async def test_room_opened_carries_the_sid_the_name_and_the_channel(held: Held) -> None:
    watching(held)
    held.room.connect()
    await held.settled()
    opened = held.recording.of("room.opened")[0]
    assert opened.data == {"name": CALL, "sid": "RM_fake", "channel": "phone"}


async def test_the_agents_own_seat_is_written_right_behind_room_opened(held: Held) -> None:
    watching(held)
    held.room.connect()
    await held.settled()
    assert held.recording.types[:2] == ["room.opened", "participant.joined"]
    assert held.recording.entries[1].data["kind"] == "agent"


async def test_a_second_sip_leg_is_kind_sip_and_never_the_caller(held: Held) -> None:
    watching(held)
    held.room.connect()
    await held.settled()
    held.room.join(a_caller(CALLER))
    held.room.join(a_caller("+59895555"))
    await held.settled()
    kinds = [entry.data["kind"] for entry in held.recording.of("participant.joined")]
    assert kinds == ["agent", "caller", "sip"]


@pytest.mark.parametrize(("scope", "kind"), [("supervise", "supervisor"), ("observe", "listener")])
async def test_a_token_scope_says_who_took_the_seat(held: Held, scope: str, kind: str) -> None:
    watching(held)
    held.room.connect()
    await held.settled()
    held.room.join(FakeParticipant("ana", attributes={SCOPE_ATTRIBUTE: scope}))
    await held.settled()
    assert held.recording.of("participant.joined")[-1].data["kind"] == kind


async def test_a_widget_is_the_caller_of_a_web_call(held: Held) -> None:
    facts = Facts(held.holding.writing, "web", "web_ab12cd34ef56")
    facts.watch(held.holding.room)
    held.room.connect()
    await held.settled()
    held.room.join(a_widget())
    await held.settled()
    assert held.recording.of("participant.joined")[-1].data["kind"] == "caller"


async def test_speaking_is_written_on_the_change_and_not_on_every_tick(held: Held) -> None:
    watching(held)
    caller = a_caller(CALLER)
    held.room.connect()
    await held.settled()
    held.room.join(caller)
    held.room.speaking(caller)
    held.room.speaking(caller)
    held.room.speaking(caller)
    await held.settled()
    assert len(held.recording.of("participant.speaking")) == 1


async def test_seats_taken_before_the_agent_arrived_are_written_behind_room_opened(
    held: Held,
) -> None:
    watching(held)
    caller = a_caller(CALLER)
    held.room.remote_participants[caller.identity] = caller
    held.room.connect()
    await held.settled()
    assert held.recording.types == [
        "room.opened",
        "participant.joined",
        "participant.joined",
        "track.published",
    ]


async def test_stopping_lets_go_of_the_room(held: Held) -> None:
    facts = watching(held)
    facts.stop()
    held.room.connect()
    held.room.join(a_caller(CALLER))
    await held.settled()
    assert held.recording.entries == []


def _as_a_log(written: list[Written]) -> list[Entry]:
    """The recorded entries as the numbered log the gateway would hold, for the reducer."""
    return [
        Entry(
            seq=seq,
            ts=time.time(),
            call=CALL,
            agent="clinica-norte",
            type=one.type,
            ephemeral=bool(one.ephemeral),
            data=one.data,
        )
        for seq, one in enumerate(written, start=1)
    ]

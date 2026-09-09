"""The five room verbs: each one's API call, its fact, and its error when the server says no."""

from __future__ import annotations

from typing import Any

import pytest
from livekit import rtc
from livekit.agents.types import NOT_GIVEN

from pinecall.session.voice import commands
from pinecall.session.voice.room import Facts
from pinecall.session.voice.room.holding import ROOM_VERB_FAILED
from pinecall_protocol import Command, ProtocolError
from tests.session.voice.room.fakes import TRUNK, FakeApi, Held, a_caller, a_held_room
from tests.session.voice.test_commands import End, Prompt, Recorded, Session

pytestmark = pytest.mark.unit

CALLER = "+59897777"
OTHER = "+59895555"


def a_command(type: str, data: dict[str, Any]) -> Command:
    return Command(type=type, agent="clinica-norte", call="call_1", data=data)


async def applied(held: Held, type: str, data: dict[str, Any], live: Session | None = None) -> None:
    """One room verb through the dispatch table, onto this held room."""
    applying = commands.Applying(live or Session(), Prompt(), End(), Recorded(), held.holding)  # pyright: ignore[reportArgumentType]
    await commands.apply(applying, a_command(type, data))
    await held.settled()


@pytest.fixture
async def held() -> Held:
    room = a_held_room()
    Facts(room.holding.writing, "phone", CALLER).watch(room.holding.room)
    room.room.connect()
    room.room.join(a_caller(CALLER))
    return room


async def test_room_invite_dials_a_sip_leg_into_this_room_and_the_room_writes_its_arrival(
    held: Held,
) -> None:
    await applied(held, "room.invite", {"to": OTHER, "kind": "sip"})
    (dialled,) = held.api.requests
    assert dialled.method == "CreateSIPParticipant"
    assert dialled.request.sip_trunk_id == TRUNK
    assert dialled.request.sip_call_to == OTHER
    assert dialled.request.room_name == held.room.name
    assert dialled.request.participant_identity == f"sip_{OTHER}"

    held.room.join(a_caller(OTHER))
    await held.settled()
    joined = held.recording.of("participant.joined")[-1]
    assert (joined.data["identity"], joined.data["kind"]) == (f"sip_{OTHER}", "sip")


async def test_room_invite_of_a_participant_is_refused_by_name_without_dialling(held: Held) -> None:
    await applied(held, "room.invite", {"to": "ana", "kind": "participant"})
    assert held.api.requests == []
    (error,) = held.recording.of("error")
    assert error.data["command"] == "room.invite"


async def test_room_invite_with_no_trunk_lands_an_error_naming_the_verb() -> None:
    held = a_held_room(trunk=None)
    await applied(held, "room.invite", {"to": OTHER, "kind": "sip"})
    assert held.api.requests == []
    assert held.recording.of("error")[0].data["command"] == "room.invite"


async def test_participant_mute_mutes_their_microphone_and_writes_track_unpublished(
    held: Held,
) -> None:
    await applied(held, "participant.mute", {"identity": f"sip_{CALLER}"})
    (muted,) = held.api.requests
    assert muted.method == "MutePublishedTrack"
    assert (muted.request.room, muted.request.identity) == (held.room.name, f"sip_{CALLER}")
    assert (muted.request.track_sid, muted.request.muted) == ("TR_microphone", True)
    (gone,) = held.recording.of("track.unpublished")
    assert gone.data == {"identity": f"sip_{CALLER}", "kind": "audio", "source": "microphone"}


async def test_participant_mute_of_somebody_not_in_the_room_is_an_error_not_a_call(
    held: Held,
) -> None:
    await applied(held, "participant.mute", {"identity": "nobody"})
    assert held.api.requests == []
    assert held.recording.of("error")[0].data["command"] == "participant.mute"


async def test_participant_remove_puts_them_out_and_the_room_writes_why_they_left(
    held: Held,
) -> None:
    await applied(held, "participant.remove", {"identity": f"sip_{CALLER}"})
    (removed,) = held.api.requests
    assert removed.method == "RemoveParticipant"
    assert (removed.request.room, removed.request.identity) == (held.room.name, f"sip_{CALLER}")

    caller = held.room.remote_participants[f"sip_{CALLER}"]
    held.room.leave(caller, rtc.DisconnectReason.PARTICIPANT_REMOVED)
    await held.settled()
    (left,) = held.recording.of("participant.left")
    assert left.data == {"identity": f"sip_{CALLER}", "reason": "participant_removed"}


async def test_room_send_publishes_on_the_topic_and_logs_the_size_never_the_payload(
    held: Held,
) -> None:
    card = {"title": "Su turno", "at": "10:15"}
    await applied(held, "room.send", {"topic": "pinecall.ui", "data": card, "to": "web_1"})
    (packet,) = held.room.sent
    assert (packet.topic, packet.payload, packet.to) == ("pinecall.ui", card, ["web_1"])
    (sent,) = held.recording.of("room.sent")
    assert sent.data == {
        "topic": "pinecall.ui",
        "to": "web_1",
        "bytes": len(b'{"title":"Su turno","at":"10:15"}'),
    }
    assert "title" not in str(held.recording.entries)


async def test_room_send_to_nobody_in_particular_reaches_the_whole_room(held: Held) -> None:
    await applied(held, "room.send", {"topic": "shop.cart", "data": {}})
    assert held.room.sent[0].to == []
    assert held.recording.of("room.sent")[0].data["to"] is None


async def test_agent_reply_is_the_sessions_generate_reply_with_its_interruptibility(
    held: Held,
) -> None:
    live = Session()
    await applied(held, "agent.reply", {"instructions": "offer the 10:15"}, live)
    await applied(
        held, "agent.reply", {"instructions": "read the terms", "allow_interruptions": False}, live
    )
    assert live.replied == [("offer the 10:15", NOT_GIVEN), ("read the terms", False)]
    assert held.api.requests == [] and held.recording.of("error") == []


@pytest.mark.parametrize(
    ("type", "data"),
    [
        ("room.invite", {"to": OTHER, "kind": "sip"}),
        ("participant.mute", {"identity": f"sip_{CALLER}"}),
        ("participant.remove", {"identity": f"sip_{CALLER}"}),
    ],
)
async def test_a_server_that_says_no_lands_an_error_naming_the_verb_and_the_call_goes_on(
    type: str, data: dict[str, Any]
) -> None:
    held = a_held_room(api=FakeApi(refusing="twirp error unavailable: sip is down"))
    held.room.join(a_caller(CALLER))
    await applied(held, type, data)
    (error,) = held.recording.of("error")
    assert error.data["command"] == type
    assert error.data["code"] == ROOM_VERB_FAILED
    assert error.data["message"] == "twirp error unavailable: sip is down"
    assert error.data["recoverable"] is True


async def test_a_room_verb_on_a_call_with_no_room_is_refused_by_name() -> None:
    applying = commands.Applying(Session(), Prompt(), End(), Recorded(), None)  # pyright: ignore[reportArgumentType]
    with pytest.raises(ProtocolError, match="room.send needs the room"):
        await commands.apply(applying, a_command("room.send", {"topic": "t", "data": {}}))

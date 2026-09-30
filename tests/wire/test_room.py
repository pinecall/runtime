"""Tests for the room's events: each is registered under its type and read back from its entry."""

from pinecall.domain.names import JsonObject
from pinecall.wire.events import EVENTS, event_of
from pinecall.wire.frames import Entry
from pinecall.wire.room import ParticipantJoined, RoomSent

ROOMS = {
    "room.opened",
    "participant.joined",
    "participant.left",
    "participant.speaking",
    "track.published",
    "track.unpublished",
    "room.sent",
}


def test_every_event_of_the_room_is_in_the_logs_registry() -> None:
    assert {kind for kind, model in EVENTS.items() if model.__module__.endswith(".room")} == ROOMS


def test_a_joined_seat_keeps_its_attributes_verbatim_and_a_payload_only_its_size() -> None:
    joined: JsonObject = {
        "identity": "sip_1",
        "kind": "sip",
        "attributes": {"sip.phoneNumber": "+1"},
    }
    entry = Entry(
        seq=1, ts=1.0, call="c", agent="a", type="participant.joined", ephemeral=False, data=joined
    )
    read = event_of(entry)
    assert isinstance(read, ParticipantJoined)
    assert read.attributes == {"sip.phoneNumber": "+1"}
    assert RoomSent(topic="t", bytes=12).written() == {"topic": "t", "bytes": 12}

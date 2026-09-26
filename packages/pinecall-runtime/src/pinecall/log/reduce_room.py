"""How the room's facts and the outside world's fold into the State; reduce.py registers these."""

from collections.abc import Callable
from typing import Any

from pinecall_protocol import room
from pinecall_protocol.codec import encode
from pinecall_protocol.envelope import Entry
from pinecall_protocol.state import Participant, ReceivedEvent, Room, State


def on_room_opened(state: State, data: room.RoomOpened) -> None:
    state.room = Room(name=data.name, sid=data.sid, participants=[], caller=None)


# joined_at is the entry's ts: the room said when, the event did not have to repeat it.
def on_participant_joined(state: State, entry: Entry, data: room.ParticipantJoined) -> None:
    if state.room is None:
        return
    arrived = {**encode(data), "joined_at": entry.ts, "speaking": False}
    state.room.participants.append(Participant.model_validate(arrived))
    if data.kind == "caller":
        state.room.caller = data.identity


def on_participant_left(state: State, data: room.ParticipantLeft) -> None:
    if state.room is None:
        return
    index = _seat_of(state.room, data.identity)
    if index is not None:
        del state.room.participants[index]
    if state.room.caller == data.identity:
        state.room.caller = None


def on_participant_speaking(state: State, data: room.ParticipantSpeaking) -> None:
    if state.room is None:
        return
    index = _seat_of(state.room, data.identity)
    if index is not None:
        state.room.participants[index].speaking = data.speaking


# The fact is kept by name and origin; its data stays in the log at that seq.
def on_event_received(state: State, entry: Entry, data: room.EventReceived) -> None:
    fact = encode(data)
    fact.pop("data")
    state.events.append(ReceivedEvent.model_validate({**fact, "seq": entry.seq}))


# An identity is unique within a room: LiveKit disconnects the first of two that share one.
def _seat_of(the_room: Room, identity: str) -> int | None:
    for index, one in enumerate(the_room.participants):
        if one.identity == identity:
            return index
    return None


# track.published, track.unpublished and room.sent are facts the log keeps and the state does not.
HANDLERS: dict[str, Callable[[State, Any], None]] = {
    "room.opened": on_room_opened,
    "participant.left": on_participant_left,
    "participant.speaking": on_participant_speaking,
}

HANDLERS_WITH_ENTRY: dict[str, Callable[[State, Entry, Any], None]] = {
    "participant.joined": on_participant_joined,
    "event.received": on_event_received,
}

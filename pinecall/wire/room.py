"""The room's events: the room opened, who joined, left and spoke, what they published, sent."""

from pinecall.domain.names import Channel, JsonObject
from pinecall.wire.frames import WireModel
from pinecall.wire.parts import ParticipantKind, TrackKind, TrackSource


class RoomOpened(WireModel):
    """The LiveKit room exists and the call lives in it; a text session never logs this."""

    name: str
    sid: str
    channel: Channel


# The attributes travel verbatim: the caller's number is a fact of the room, not a field of ours.
class ParticipantJoined(WireModel):
    """Somebody joined the room."""

    identity: str
    kind: ParticipantKind
    name: str | None = None
    attributes: JsonObject


class ParticipantLeft(WireModel):
    """Somebody left the room; when it is the caller, call.ended follows."""

    identity: str
    reason: str


class ParticipantSpeaking(WireModel):
    """The room's own voice activity for one participant flipped; a light for the console."""

    identity: str
    speaking: bool


class TrackPublished(WireModel):
    """A participant put a track on the room: their microphone, their camera, a screen."""

    identity: str
    kind: TrackKind
    source: TrackSource


class TrackUnpublished(WireModel):
    """A participant's track left the room."""

    identity: str
    kind: TrackKind
    source: TrackSource


class RoomSent(WireModel):
    """The agent pushed a payload to a browser in the room; the payload stays out of the log."""

    topic: str
    to: str | None = None
    bytes: int

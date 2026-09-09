"""participant.mute: their microphone silenced for everyone in the room, for good."""

from __future__ import annotations

from livekit import rtc
from livekit.protocol.room import MuteRoomTrackRequest

from pinecall.session.voice.room.holding import Holding
from pinecall_protocol.commands import ParticipantMute
from pinecall_protocol.room import TrackUnpublished

VERB = "participant.mute"

NOT_IN_THE_ROOM = "participant.mute: {identity} is not in the room"
NO_MICROPHONE = "participant.mute: {identity} has no microphone in the room"


# livekit mutes a track in place; nothing is unpublished on the media plane. The protocol's word
# for "their audio left the room" is track.unpublished, so this verb writes it itself, once the
# server said yes: the room emits no fact of its own that a reader could take for this one.
async def muted(holding: Holding, wanted: ParticipantMute) -> None:
    """MutePublishedTrack on their microphone, then track.unpublished for it."""
    seat = holding.room.remote_participants.get(wanted.identity)
    if seat is None:
        holding.failed(VERB, NOT_IN_THE_ROOM.format(identity=wanted.identity))
        return
    microphone = _the_microphone(seat)
    if microphone is None:
        holding.failed(VERB, NO_MICROPHONE.format(identity=wanted.identity))
        return
    request = MuteRoomTrackRequest(
        room=holding.room.name, identity=wanted.identity, track_sid=microphone.sid, muted=True
    )
    try:
        await holding.api.room.mute_published_track(request)
    except Exception as refused:  # noqa: BLE001 — every way the server says no is the same here
        holding.failed(VERB, str(refused))
        return
    holding.writing.later(
        "track.unpublished",
        TrackUnpublished(identity=wanted.identity, kind="audio", source="microphone"),
    )


def _the_microphone(seat: rtc.RemoteParticipant) -> rtc.RemoteTrackPublication | None:
    """The one audio track livekit sourced from their microphone, or None when they have none."""
    for publication in seat.track_publications.values():
        if publication.source == rtc.TrackSource.SOURCE_MICROPHONE:
            return publication
    return None

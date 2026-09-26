"""The room's events as the facts they are: who joined, who spoke, what they published, who left."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence
from typing import Any

from livekit import rtc

# The Literal of every event `rtc.Room.on` accepts: livekit declares it beside Room itself, and it
# is what the room's signature is written in, so our names are typed in the room's own words.
from livekit.rtc.room import EventTypes as RoomEvent
from pydantic import ValidationError

from pinecall.session.voice import sip
from pinecall.session.voice.log_writer import Writing
from pinecall.types.scopes import SCOPE_ATTRIBUTE
from pinecall_protocol import defs
from pinecall_protocol.events import DtmfReceived
from pinecall_protocol.room import (
    ParticipantJoined,
    ParticipantLeft,
    ParticipantSpeaking,
    RoomOpened,
    TrackPublished,
    TrackUnpublished,
)

# The room's own event names. Named once, because the subscribe and the unsubscribe are two lists
# that must never drift apart. The Python SDK never emits "connected": the state change is the
# event, and it is the one room_io.py:181 listens on too.
CONNECTION: RoomEvent = "connection_state_changed"
JOINED: RoomEvent = "participant_connected"
LEFT: RoomEvent = "participant_disconnected"
PUBLISHED: RoomEvent = "track_published"
UNPUBLISHED: RoomEvent = "track_unpublished"
SPEAKERS: RoomEvent = "active_speakers_changed"
DTMF: RoomEvent = "sip_dtmf_received"

# Who else hears a tone the caller keyed, and when: the code a page shows is keyed on the phone
# (session/voice/room/code_claim.py). A listener and not a second subscriber, because telling the
# caller's leg from any other is this class's to know, and it knows it once.
type Heard = Callable[[str, float], None]

# The seat a token's scope takes, in ParticipantKind's words. Any other scope is the person the
# agent serves.
KIND_OF_SCOPE: dict[str, defs.ParticipantKind] = {"supervise": "supervisor", "observe": "listener"}

# livekit's own words for a track, in ours. A data track has no word on the wire and is not a fact.
TRACK_KINDS: dict[int, defs.TrackKind] = {
    rtc.TrackKind.KIND_AUDIO: "audio",
    rtc.TrackKind.KIND_VIDEO: "video",
}
TRACK_SOURCES: dict[int, defs.TrackSource] = {
    rtc.TrackSource.SOURCE_MICROPHONE: "microphone",
    rtc.TrackSource.SOURCE_CAMERA: "camera",
    rtc.TrackSource.SOURCE_SCREENSHARE: "screen_share",
    rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO: "screen_share_audio",
    rtc.TrackSource.SOURCE_UNKNOWN: "unknown",
}

# A participant that left with no reason livekit could name.
LEFT_UNSAID = "unknown"


# One subscriber per room, holding what it needs to tell two facts apart: who the call was
# resolved for, so a second SIP leg is not the caller; and who was speaking a moment ago, so
# participant.speaking is written on the change and not on every tick.
class Facts:
    """One room's events, written to the log in the order LiveKit produced them."""

    def __init__(
        self, writing: Writing, channel: defs.Channel, caller: str, heard: Heard | None = None
    ) -> None:
        self._writing = writing
        self._heard = heard
        self._channel: defs.Channel = channel
        self._caller = caller
        self._room: rtc.Room | None = None
        self._listening: dict[RoomEvent, Callable[..., None]] = {}
        self._speaking: set[str] = set()
        self._opening: asyncio.Task[None] | None = None

    def watch(self, room: rtc.Room) -> None:
        """Subscribe to the room: room.opened is the first fact, on connect or already connected."""
        self._room = room
        self._listening = {
            CONNECTION: self._connection_changed,
            JOINED: self._joined,
            LEFT: self._left,
            PUBLISHED: self._published,
            UNPUBLISHED: self._unpublished,
            SPEAKERS: self._speakers_changed,
            DTMF: self._a_tone,
        }
        for name, callback in self._listening.items():
            room.on(name, callback)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        # The job joins the room before it knows who the call is for, so by the time the bridge
        # exists the connection has usually happened and its event is behind us.
        if room.isconnected():
            self._connection_changed(rtc.ConnectionState.CONN_CONNECTED)

    def stop(self) -> None:
        """Let go of the room: the call is over."""
        if self._opening is not None:
            self._opening.cancel()
            self._opening = None
        room = self._room
        if room is None:
            return
        for name, callback in self._listening.items():
            room.off(name, callback)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        self._room = None

    # ── the room itself ─────────────────────────────────────────────────────────

    # The sid arrives with the connect result and Room.sid is async for the rare case it has not
    # yet; so the opening is a task, and it writes the seats already taken behind room.opened.
    def _connection_changed(self, state: int) -> None:
        """Connected: room.opened, then whoever was in the room before the agent arrived."""
        if state == rtc.ConnectionState.CONN_CONNECTED and self._opening is None:
            self._opening = asyncio.ensure_future(self._opened())

    async def _opened(self) -> None:
        """room.opened with the room's sid, then participant.joined for every seat, ours first."""
        room = self._room
        if room is None:
            return
        opened = RoomOpened(name=room.name, sid=await room.sid, channel=self._channel)
        await self._writing.emit("room.opened", opened)
        self._joined(room.local_participant)
        for participant in room.remote_participants.values():
            self._joined(participant)
            for publication in participant.track_publications.values():
                self._published(publication, participant)

    # ── the seats ───────────────────────────────────────────────────────────────

    def _joined(self, participant: rtc.Participant) -> None:
        """participant.joined, attributes verbatim: the caller's number is a fact of the room."""
        attributes = dict(participant.attributes)
        self._writing.later(
            "participant.joined",
            ParticipantJoined(
                identity=participant.identity,
                kind=self._kind_of(participant, attributes),
                name=participant.name or None,
                attributes=attributes,
            ),
        )

    def _left(self, participant: rtc.RemoteParticipant) -> None:
        """participant.left, with livekit's own reason in lower case: participant_removed, ..."""
        reason = participant.disconnect_reason
        said = LEFT_UNSAID if reason is None else rtc.DisconnectReason.Name(reason).lower()
        self._writing.later(
            "participant.left", ParticipantLeft(identity=participant.identity, reason=said)
        )
        self._speaking.discard(participant.identity)

    # Who a participant is to the call. livekit says which is the agent; a token says who came to
    # supervise or to listen; SIP attributes say which leg is a phone — and the phone whose number
    # the call was resolved for is the caller, any other is a leg room.invite brought in.
    def _kind_of(
        self, participant: rtc.Participant, attributes: dict[str, str]
    ) -> defs.ParticipantKind:
        """The seat this participant takes, in ParticipantKind's words."""
        if participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            return "agent"
        by_scope = KIND_OF_SCOPE.get(attributes.get(SCOPE_ATTRIBUTE, ""))
        if by_scope is not None:
            return by_scope
        is_a_phone = any(key.startswith(sip.SIP_PREFIX) for key in attributes)
        if is_a_phone and sip.sip_numbers(attributes).caller != self._caller:
            return "sip"
        return "caller"

    # ── the tracks ──────────────────────────────────────────────────────────────

    def _published(self, publication: rtc.TrackPublication, participant: rtc.Participant) -> None:
        """track.published: a microphone, a camera, a screen — never a data track."""
        track = _a_track(publication)
        if track is not None:
            kind, source = track
            self._writing.later(
                "track.published",
                TrackPublished(identity=participant.identity, kind=kind, source=source),
            )

    def _unpublished(self, publication: rtc.TrackPublication, participant: rtc.Participant) -> None:
        """track.unpublished: the same track, gone."""
        track = _a_track(publication)
        if track is not None:
            kind, source = track
            self._writing.later(
                "track.unpublished",
                TrackUnpublished(identity=participant.identity, kind=kind, source=source),
            )

    # ── the voices ──────────────────────────────────────────────────────────────

    def _speakers_changed(self, speakers: Sequence[rtc.Participant]) -> None:
        """participant.speaking for whoever started and whoever stopped; nothing for the rest."""
        now = {speaker.identity for speaker in speakers}
        for identity in sorted(now - self._speaking):
            self._writing.later(
                "participant.speaking", ParticipantSpeaking(identity=identity, speaking=True)
            )
        for identity in sorted(self._speaking - now):
            self._writing.later(
                "participant.speaking", ParticipantSpeaking(identity=identity, speaking=False)
            )
        self._speaking = now

    # ── the keypad ──────────────────────────────────────────────────────────────

    # Only the caller's own leg: a second leg room.invite brought in is somebody else's keypad, and
    # a tone a server SDK sent names no participant at all.
    def _a_tone(self, tone: rtc.SipDTMF) -> None:
        """dtmf.received for a tone the caller keyed, then the listener; another leg's, nothing."""
        participant = tone.participant
        if participant is None:
            return
        if self._kind_of(participant, dict(participant.attributes)) != "caller":
            return
        try:
            keyed = DtmfReceived.model_validate({"digit": tone.digit, "code": tone.code})
        # A digit the wire has no word for is not a fact, as a data track is not.
        except ValidationError:
            return
        self._writing.later("dtmf.received", keyed)
        if self._heard is not None:
            self._heard(keyed.digit, time.monotonic())


def _a_track(publication: Any) -> tuple[defs.TrackKind, defs.TrackSource] | None:
    """The track in the wire's words, or None for a kind the wire has no word for."""
    kind = TRACK_KINDS.get(publication.kind)
    if kind is None:
        return None
    return kind, TRACK_SOURCES.get(publication.source, "unknown")

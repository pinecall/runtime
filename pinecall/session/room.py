"""A voice call's room: the caller's leg, transfers, tones, the melody, and the room's facts."""

import asyncio
import contextlib
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from livekit import api, rtc
from livekit.agents import utils
from livekit.agents.voice import AgentSession
from livekit.agents.voice.background_audio import AudioConfig, BackgroundAudioPlayer, PlayHandle
from livekit.protocol.room import MuteRoomTrackRequest, RoomParticipantIdentity
from livekit.protocol.sip import (
    CreateSIPParticipantRequest,
    SIPOutboundConfig,
    SIPTransferReason,
    SIPTransferStatus,
    TransferSIPParticipantRequest,
    TransferSIPParticipantResponse,
)
from livekit.rtc.participant import PublishDataError, PublishDTMFError
from pydantic import ValidationError

from pinecall.domain.types import SCOPE_ATTRIBUTE, Channel, JsonObject
from pinecall.session.call import Call
from pinecall.wire import events as wire
from pinecall.wire.commands import (
    CallDtmf,
    CallTransfer,
    ParticipantMute,
    ParticipantRemove,
    RoomInvite,
    RoomSend,
)
from pinecall.wire.frames import WireModel
from pinecall.wire.parts import ParticipantKind, TrackKind, TrackSource, TransferMode

logger = logging.getLogger(__name__)

# The caller's leg can join a moment after the agent, and livekit's own wait never times out.
WAIT_FOR_THE_LEG_S = 5.0
# livekit's attribute names for the caller's number and the one dialled.
SIP_PREFIX = "sip."
CALLER_NUMBER = f"{SIP_PREFIX}phoneNumber"
DIALLED_NUMBER = f"{SIP_PREFIX}trunkPhoneNumber"
# livekit's convention for a dialled leg's identity, which every dialled leg follows.
LEG_PREFIX = "sip_"
KIND_OF_SCOPE: dict[str, ParticipantKind] = {"supervise": "supervisor", "observe": "listener"}

# A warm transfer rings for less than livekit's 30 s; a tone plays meanwhile, so silence is not
# taken for a dropped call.
RINGING_S = 25.0
# The longest a transfer asked outside a tool waits for the agent to finish its sentence.
THE_ANNOUNCEMENT_S = 12.0

# RFC 4733 codes, as publish_dtmf takes them. A comma pauses, as in a dialler's string; any
# other character refuses the whole sequence, since half a card number is worse than none. Some
# carriers merge tones sent back to back into one long press.
CODES: dict[str, int] = {**{str(digit): digit for digit in range(10)}, "*": 10, "#": 11}
PAUSE = ","
PAUSE_S = 0.5
BETWEEN_TONES_S = 0.12

# A code is four digits keyed within eight seconds (docs/protocol/codes.md); answers to a menu
# are slower.
CODE_LENGTH = 4
CODE_WITHIN_S = 8.0

# The hold melody sits under the agent's voice, since a phone leg has no volume of its own. A
# tool that answers inside the grace plays nothing.
VOLUME = 0.6
GRACE_S = 2.5
FADE_IN_S = 0.4
FADE_OUT_S = 0.3

ROOM_VERB_FAILED = "room_verb_failed"
NO_LEG = "{verb}: this call has no phone leg"
NO_TRUNK = "{verb}: no outbound SIP trunk is configured, so {to} cannot be dialled"
NOT_A_TONE = "call.dtmf: {digit!r} is not a touch tone (0-9, * or #), and nothing was sent"
NOT_DIALLED = "room.invite: a participant joins with a token; the room dials only kind sip"
NOT_IN_THE_ROOM = "participant.mute: {identity} is not in the room"
NO_MICROPHONE = "participant.mute: {identity} has no microphone in the room"

TRACK_KINDS: dict[int, TrackKind] = {
    rtc.TrackKind.KIND_AUDIO: "audio",
    rtc.TrackKind.KIND_VIDEO: "video",
}
TRACK_SOURCES: dict[int, TrackSource] = {
    rtc.TrackSource.SOURCE_MICROPHONE: "microphone",
    rtc.TrackSource.SOURCE_CAMERA: "camera",
    rtc.TrackSource.SOURCE_SCREENSHARE: "screen_share",
    rtc.TrackSource.SOURCE_SCREENSHARE_AUDIO: "screen_share_audio",
}


# The SFU keeps no outbound trunk: each leg carries its own, so a restarted SFU dials on.
@dataclass(frozen=True)
class Trunk:
    """How a number is dialled: the trunk inline, and the org's number shown."""

    config: SIPOutboundConfig
    shown: str | None = None


# Asked each time a verb dials: the gateway checks the org's dial guards and its limits first,
# so a refusal (NotAllowed) carries their reason into the log. Never cached.
type Trunks = Callable[[str], Awaitable[Trunk]]
# A code the caller keyed, claimed with the gateway, which decides whether it is one.
type Claim = Callable[[str], Awaitable[None]]


class Room:
    """One voice call's room, the server's API over it, and what it writes about both."""

    def __init__(
        self, call: Call, room: rtc.Room, server: api.LiveKitAPI, *, trunks: Trunks, claim: Claim
    ) -> None:
        """The room of a call not yet opened."""
        self.call = call
        self.room = room
        self.server = server
        self.trunks = trunks
        self.claim = claim
        self.speaking: set[str] = set()
        self.keyed: deque[tuple[str, float]] = deque(maxlen=CODE_LENGTH)
        self.claimed: set[str] = set()
        self.tasks: set[asyncio.Task[None]] = set()
        self.bridged: str | None = None

    # ── the caller's leg ──

    async def leg(self) -> rtc.RemoteParticipant | None:
        """The caller's SIP leg, waited for a moment; none on a call that is not a phone's."""
        return await caller_leg(self.room, self.call.context.channel)

    # ── transfers ──

    async def mode_of(self, wanted: CallTransfer) -> TransferMode:
        """The mode asked for, else cold on a phone leg and warm on a browser's call."""
        if wanted.mode is not None:
            return wanted.mode
        return "cold" if await self.leg() is not None else "warm"

    # A failed transfer is call.transferred with ok false, and the call goes on.
    async def transfer(
        self, wanted: CallTransfer, live: AgentSession[None]
    ) -> wire.CallTransferred:
        """Send the caller on (cold, by REFER of their leg) or dial the person in (warm)."""
        mode = await self.mode_of(wanted)
        await _after_the_announcement(live)
        if mode == "cold":
            return await self._sent_on(wanted)
        return await self._dialled_in(wanted)

    # The person is dialled into this room. The caller leaving closes the session by itself; the
    # person leaving does not, so the call ends then.
    def when_the_person_leaves(self, identity: str, hang_up: Callable[[], None]) -> None:
        """Call `hang_up` once the transferred leg leaves the room."""
        self.bridged = identity

        def left(participant: rtc.RemoteParticipant) -> None:
            if participant.identity == self.bridged:
                self.bridged = None
                hang_up()

        self.room.on("participant_disconnected", left)  # pyright: ignore[reportUnknownMemberType]

    async def _sent_on(self, wanted: CallTransfer) -> wire.CallTransferred:
        leg = await self.leg()
        if leg is None:
            return _stayed(wanted, "cold", NO_LEG.format(verb="call.transfer"))
        request = TransferSIPParticipantRequest(
            participant_identity=leg.identity,
            room_name=self.room.name,
            transfer_to=wanted.to,
            play_dialtone=True,
        )
        try:
            answer = await self.server.sip.transfer_sip_participant(request)
        except api.TwirpError as refused:
            return _stayed(wanted, "cold", f"call.transfer: {refused}")
        # A transfer can fail without raising.
        if answer.status != SIPTransferStatus.STS_TRANSFER_SUCCESSFUL:
            return _stayed(wanted, "cold", _did_not_take(answer))
        return wire.CallTransferred(to=wanted.to, mode="cold", ok=True)

    # Waiting until answered is what makes busy and no answer seen; without it the request
    # returns as soon as the INVITE went out.
    async def _dialled_in(self, wanted: CallTransfer) -> wire.CallTransferred:
        request = await self._leg_to(wanted.to, verb="call.transfer")
        if isinstance(request, str):
            return _stayed(wanted, "warm", request)
        request.wait_until_answered = True
        request.play_dialtone = True
        request.ringing_timeout.FromTimedelta(timedelta(seconds=RINGING_S))
        try:
            await self.server.sip.create_sip_participant(request)
        except api.TwirpError as refused:
            return _stayed(wanted, "warm", f"call.transfer: {refused}")
        return wire.CallTransferred(to=wanted.to, mode="warm", ok=True)

    # ── the other room verbs ──

    async def apply(self, command: WireModel) -> None:
        """A room verb of the app's; the server's refusal is an error entry, the call goes on."""
        match command:
            case RoomInvite():
                await self._invite(command)
            case RoomSend():
                await self._send(command)
            case ParticipantMute():
                await self._mute(command)
            case ParticipantRemove():
                request = RoomParticipantIdentity(room=self.room.name, identity=command.identity)
                await self._asked(
                    "participant.remove", self.server.room.remove_participant(request)
                )
            case CallDtmf():
                await self._tones(command)
            case _:
                self._failed(
                    type(command).__name__,
                    f"{type(command).__name__} is not something this call does",
                )

    # Success is written by the room itself, as participant.joined of kind sip.
    async def _invite(self, wanted: RoomInvite) -> None:
        if wanted.kind != "sip":
            self._failed("room.invite", NOT_DIALLED)
            return
        request = await self._leg_to(wanted.to, verb="room.invite")
        if isinstance(request, str):
            self._failed("room.invite", request)
            return
        await self._asked("room.invite", self.server.sip.create_sip_participant(request))

    # The payload stays out of the log: room.sent says the topic, whom, and the size.
    async def _send(self, wanted: RoomSend) -> None:
        packed = json.dumps(wanted.data, separators=(",", ":")).encode()
        to = [] if wanted.to is None else [wanted.to]
        sent = self.room.local_participant.publish_data(
            packed, topic=wanted.topic, destination_identities=to
        )
        if await self._asked("room.send", sent):
            self.call.writing.write(
                "room.sent", wire.RoomSent(topic=wanted.topic, to=wanted.to, bytes=len(packed))
            )

    # livekit mutes in place and says nothing, so the verb writes track.unpublished itself.
    async def _mute(self, wanted: ParticipantMute) -> None:
        seat = self.room.remote_participants.get(wanted.identity)
        if seat is None:
            self._failed("participant.mute", NOT_IN_THE_ROOM.format(identity=wanted.identity))
            return
        microphone = next(
            (
                one
                for one in seat.track_publications.values()
                if one.source == rtc.TrackSource.SOURCE_MICROPHONE
            ),
            None,
        )
        if microphone is None:
            self._failed("participant.mute", NO_MICROPHONE.format(identity=wanted.identity))
            return
        request = MuteRoomTrackRequest(
            room=self.room.name, identity=wanted.identity, track_sid=microphone.sid, muted=True
        )
        if await self._asked("participant.mute", self.server.room.mute_published_track(request)):
            unpublished = wire.TrackUnpublished(
                identity=wanted.identity, kind="audio", source="microphone"
            )
            self.call.writing.write("track.unpublished", unpublished)

    async def _tones(self, wanted: CallDtmf) -> None:
        wrong = next(
            (digit for digit in wanted.digits if digit != PAUSE and digit not in CODES), None
        )
        if wrong is not None:
            self._failed("call.dtmf", NOT_A_TONE.format(digit=wrong))
            return
        if await self.leg() is None:
            self._failed("call.dtmf", NO_LEG.format(verb="call.dtmf"))
            return
        for digit in wanted.digits:
            if digit == PAUSE:
                await asyncio.sleep(PAUSE_S)
                continue
            if not await self._asked(
                "call.dtmf",
                self.room.local_participant.publish_dtmf(code=CODES[digit], digit=digit),
            ):
                return
            await asyncio.sleep(BETWEEN_TONES_S)

    async def _leg_to(self, to: str, *, verb: str) -> CreateSIPParticipantRequest | str:
        try:
            trunk = await self.trunks(to)
        except Exception as refused:
            logger.warning("%s: no trunk to dial through", verb, exc_info=True)
            return str(refused) or NO_TRUNK.format(verb=verb, to=to)
        request = CreateSIPParticipantRequest(
            trunk=trunk.config,
            sip_call_to=to,
            room_name=self.room.name,
            participant_identity=f"{LEG_PREFIX}{to}",
        )
        if trunk.shown is not None:
            request.sip_number = trunk.shown
        return request

    # The server's own words, as it said them.
    async def _asked(self, verb: str, done: Awaitable[object]) -> bool:
        try:
            await done
        except (api.TwirpError, PublishDataError, PublishDTMFError, ConnectionError) as refused:
            self._failed(verb, str(refused))
            return False
        return True

    def _failed(self, verb: str, why: str) -> None:
        failed = wire.ErrorEvent(code=ROOM_VERB_FAILED, message=why, command=verb, recoverable=True)
        self.call.writing.write("error", failed)

    # ── the room's own facts ──

    # The job usually connects before the session exists, so the connection was already missed.
    def watch(self) -> None:
        """Write what happens in the room, room.opened first."""
        for name, listener in self._listeners().items():
            self.room.on(name, listener)  # pyright: ignore[reportUnknownMemberType]
        if self.room.isconnected():
            self._connection(rtc.ConnectionState.CONN_CONNECTED)

    def stop(self) -> None:
        """Let go of the room and of every claim still out."""
        for name, listener in self._listeners().items():
            self.room.off(name, listener)  # pyright: ignore[reportUnknownMemberType]
        for task in self.tasks:
            task.cancel()

    def _listeners(self) -> dict[rtc.room.EventTypes, Callable[..., None]]:
        return {
            "connection_state_changed": self._connection,
            "participant_connected": self._joined,
            "participant_disconnected": self._left,
            "track_published": self._published,
            "track_unpublished": self._unpublished,
            "active_speakers_changed": self._speakers,
            "sip_dtmf_received": self._tone,
        }

    def _connection(self, state: int) -> None:
        if state == rtc.ConnectionState.CONN_CONNECTED:
            self._spawn(self._opened())

    # room.sid is awaited, so the opening runs as a task: room.opened, the agent's seat, then the
    # seats taken before the agent arrived and their tracks.
    async def _opened(self) -> None:
        opened = wire.RoomOpened(
            name=self.room.name, sid=await self.room.sid, channel=self.call.context.channel
        )
        await self.call.writing.write("room.opened", opened)
        self._joined(self.room.local_participant)
        for seat in self.room.remote_participants.values():
            self._joined(seat)
            for publication in seat.track_publications.values():
                self._published(publication, seat)

    def _joined(self, seat: rtc.Participant) -> None:
        attributes: dict[str, str] = dict(seat.attributes)
        said: JsonObject = dict(attributes)
        joined = wire.ParticipantJoined(
            identity=seat.identity,
            kind=self._kind_of(seat, attributes),
            name=seat.name or None,
            attributes=said,
        )
        self.call.writing.write("participant.joined", joined)

    def _left(self, seat: rtc.RemoteParticipant) -> None:
        reason = seat.disconnect_reason
        said = "unknown" if reason is None else rtc.DisconnectReason.Name(reason).lower()
        self.call.writing.write(
            "participant.left", wire.ParticipantLeft(identity=seat.identity, reason=said)
        )
        self.speaking.discard(seat.identity)

    # A SIP seat is the caller only if its number is the call's caller; any other was dialled in.
    def _kind_of(self, seat: rtc.Participant, attributes: dict[str, str]) -> ParticipantKind:
        if seat.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT:
            return "agent"
        by_scope = KIND_OF_SCOPE.get(attributes.get(SCOPE_ATTRIBUTE, ""))
        if by_scope is not None:
            return by_scope
        a_phone = any(key.startswith(SIP_PREFIX) for key in attributes)
        if a_phone and attributes.get(CALLER_NUMBER) != self.call.context.caller:
            return "sip"
        return "caller"

    def _published(self, publication: rtc.TrackPublication, seat: rtc.Participant) -> None:
        track = _track_of(publication)
        if track is not None:
            published = wire.TrackPublished(identity=seat.identity, kind=track[0], source=track[1])
            self.call.writing.write("track.published", published)

    def _unpublished(self, publication: rtc.TrackPublication, seat: rtc.Participant) -> None:
        track = _track_of(publication)
        if track is not None:
            unpublished = wire.TrackUnpublished(
                identity=seat.identity, kind=track[0], source=track[1]
            )
            self.call.writing.write("track.unpublished", unpublished)

    # Written on the change, never on every tick.
    def _speakers(self, speakers: list[rtc.Participant]) -> None:
        now = {speaker.identity for speaker in speakers}
        for identity in sorted(now - self.speaking):
            self.call.writing.write(
                "participant.speaking", wire.ParticipantSpeaking(identity=identity, speaking=True)
            )
        for identity in sorted(self.speaking - now):
            self.call.writing.write(
                "participant.speaking", wire.ParticipantSpeaking(identity=identity, speaking=False)
            )
        self.speaking = now

    # The caller's tones only: a server's have no seat, another leg is not the caller.
    def _tone(self, tone: rtc.SipDTMF) -> None:
        seat = tone.participant
        if seat is None or self._kind_of(seat, dict(seat.attributes)) != "caller":
            return
        try:
            keyed = wire.DtmfReceived.model_validate({"digit": tone.digit, "code": tone.code})
        except ValidationError:
            return
        self.call.writing.write("dtmf.received", keyed)
        self._keyed(keyed.digit, time.monotonic())

    # The worker cannot tell a code from an extension, so every candidate is claimed and the
    # gateway decides; each code once a call. `*` or `#` starts over.
    def _keyed(self, digit: str, at: float) -> None:
        if not digit.isdigit():
            self.keyed.clear()
            return
        self.keyed.append((digit, at))
        if len(self.keyed) < CODE_LENGTH or at - self.keyed[0][1] > CODE_WITHIN_S:
            return
        code = "".join(one for one, _ in self.keyed)
        self.keyed.clear()
        if code not in self.claimed:
            self.claimed.add(code)
            self._spawn(self._claimed(code))

    async def _claimed(self, code: str) -> None:
        try:
            await self.claim(code)
        except Exception:
            logger.warning(
                "call %s: the claim of a keyed code was refused",
                self.call.context.call,
                exc_info=True,
            )

    # A task of a synchronous listener is kept, or it may be collected mid-run.
    def _spawn(self, work: Awaitable[None]) -> None:
        task = asyncio.ensure_future(work)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)


class HoldMusic:
    """The melody played while a tool runs or the line is held, shared by what overlaps."""

    def __init__(self, clip: Path) -> None:
        """A melody not yet in any room."""
        self.clip = clip
        self.player: BackgroundAudioPlayer | None = None
        self.handle: PlayHandle | None = None
        self.running = 0
        self.pending: asyncio.Task[None] | None = None
        self.quiet = asyncio.Event()
        self.quiet.set()

    # The player publishes its own track, which the SIP bridge mixes into the phone leg. A
    # player that does not start leaves the call without a melody, and nothing else.
    async def start(self, room: rtc.Room) -> None:
        """Publish the melody's track in the room."""
        player = BackgroundAudioPlayer()
        try:
            await player.start(room=room)  # pyright: ignore[reportUnknownMemberType]
        except (RuntimeError, ConnectionError):
            logger.warning(
                "the hold melody did not start: the call goes on without it", exc_info=True
            )
            return
        self.player = player

    def floor(self, *, speaking: bool) -> None:
        """Whether the agent is speaking, as the session says."""
        if speaking:
            self.quiet.clear()
        else:
            self.quiet.set()

    def began(self) -> None:
        """One more reason to play; the first starts the melody after the grace."""
        self.running += 1
        if self.running == 1 and self.player is not None:
            self.pending = asyncio.create_task(self._after_the_grace())

    def ended(self) -> None:
        """One reason less; the last stops the melody."""
        self.running = max(self.running - 1, 0)
        if self.running == 0:
            self._stop()

    async def aclose(self) -> None:
        """Stop and close the player."""
        self._stop()
        player, self.player = self.player, None
        if player is not None:
            await player.aclose()

    # The tool starts while its announcement still plays, so the grace counts from when the agent
    # is quiet; checked again after it, since livekit may speak another round's preamble.
    async def _after_the_grace(self) -> None:
        await self.quiet.wait()
        await asyncio.sleep(GRACE_S)
        if self.running == 0 or not self.quiet.is_set() or self.player is None:
            return
        melody = AudioConfig(str(self.clip), volume=VOLUME, fade_in=FADE_IN_S, fade_out=FADE_OUT_S)
        self.handle = self.player.play(melody, loop=True)

    def _stop(self) -> None:
        if self.pending is not None and not self.pending.done():
            self.pending.cancel()
        self.pending = None
        if self.handle is not None:
            self.handle.stop()
            self.handle = None


# livekit's wait never times out; a room that never connected raises, and has no leg either.
# The first SIP seat is the caller; later ones came from room.invite or a warm transfer.
async def caller_leg(room: rtc.Room, channel: Channel) -> rtc.RemoteParticipant | None:
    """The caller's SIP seat, waited for a moment on a phone call; none on any other."""
    if channel == "phone" and _phone_in(room) is None:
        with contextlib.suppress(TimeoutError, RuntimeError):
            async with asyncio.timeout(WAIT_FOR_THE_LEG_S):
                await utils.wait_for_participant(
                    room, kind=rtc.ParticipantKind.PARTICIPANT_KIND_SIP
                )
    return _phone_in(room)


def _phone_in(room: rtc.Room) -> rtc.RemoteParticipant | None:
    return next(
        (
            seat
            for seat in room.remote_participants.values()
            if seat.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        ),
        None,
    )


async def _after_the_announcement(live: AgentSession[None]) -> None:
    speech = live.current_speech
    if speech is not None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(speech.wait_for_playout(), THE_ANNOUNCEMENT_S)


def _stayed(wanted: CallTransfer, mode: TransferMode, why: str) -> wire.CallTransferred:
    return wire.CallTransferred(to=wanted.to, mode=mode, ok=False, error=why)


def _did_not_take(answer: TransferSIPParticipantResponse) -> str:
    said = SIPTransferReason.Name(answer.reason).removeprefix("STR_").lower()
    return f"call.transfer: {said}, SIP {answer.sip_status.code} {answer.sip_status.status}".strip()


def _track_of(publication: rtc.TrackPublication) -> tuple[TrackKind, TrackSource] | None:
    kind = TRACK_KINDS.get(publication.kind)
    return None if kind is None else (kind, TRACK_SOURCES.get(publication.source, "unknown"))

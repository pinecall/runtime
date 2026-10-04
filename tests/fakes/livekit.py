"""LiveKit as the suites see it: seats, a room, the SIP and rooms doors of a server, a player."""

import asyncio
import base64
import hashlib
import time
import types
from collections.abc import Mapping
from typing import override

import numpy as np
from livekit import api, rtc
from livekit.agents.voice.background_audio import (
    AudioConfig,
    AudioSource,
    BackgroundAudioPlayer,
    PlayHandle,
)
from livekit.agents.voice.io import AudioInput, AudioOutput, AudioOutputCapabilities
from livekit.api.agent_dispatch_service import AgentDispatchService
from livekit.api.room_service import RoomService
from livekit.api.sip_service import SipService
from livekit.protocol.agent_dispatch import AgentDispatch, CreateAgentDispatchRequest
from livekit.protocol.models import ListUpdate
from livekit.protocol.room import (
    DeleteRoomRequest,
    DeleteRoomResponse,
    ListParticipantsRequest,
    ListParticipantsResponse,
    ListRoomsRequest,
    ListRoomsResponse,
    MuteRoomTrackRequest,
    MuteRoomTrackResponse,
    RemoveParticipantResponse,
    RoomParticipantIdentity,
)
from livekit.protocol.sip import (
    CreateSIPDispatchRuleRequest,
    CreateSIPInboundTrunkRequest,
    CreateSIPParticipantRequest,
    DeleteSIPDispatchRuleRequest,
    DeleteSIPTrunkRequest,
    ListSIPDispatchRuleRequest,
    ListSIPDispatchRuleResponse,
    ListSIPInboundTrunkRequest,
    ListSIPInboundTrunkResponse,
    SIPDispatchRuleInfo,
    SIPInboundTrunkInfo,
    SIPMediaConfig,
    SIPOutboundConfig,
    SIPParticipantInfo,
    SIPTransferStatus,
    SIPTrunkInfo,
    TransferSIPParticipantRequest,
    TransferSIPParticipantResponse,
)
from livekit.rtc._proto import handle_pb2, participant_pb2, track_pb2
from livekit.rtc._utils import BroadcastQueue

from pinecall.domain.names import PRODUCTION, SANDBOX, Env
from tests.fakes.acme import ACME, AcmeLLM, AcmeStreamedTTS, AcmeSTT, AcmeTTS, seat

# Long enough for the JWT livekit signs with it; a secret of nothing.
A_SECRET = "a secret of thirty-two bytes or more"


def signed(body: str, key: str, secret: str = A_SECRET) -> str:
    """The token livekit sends with a webhook's body: the body's sha256 in a claim, signed."""
    digest = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    return api.AccessToken(key, secret).with_sha256(digest).to_jwt()


class Seated(rtc.LocalParticipant):
    """The agent's own seat, keeping what it published instead of sending it."""

    def __init__(self) -> None:
        """A seat that published nothing yet."""
        info = participant_pb2.ParticipantInfo(sid="PA_agent", identity="agent", kind=4)  # pyright: ignore[reportArgumentType]
        owned = participant_pb2.OwnedParticipant(handle=handle_pb2.FfiOwnedHandle(id=0), info=info)
        super().__init__(BroadcastQueue(), owned)
        self.published: list[tuple[str, bytes, list[str]]] = []
        self.tones: list[str] = []

    @override
    async def publish_data(
        self,
        payload: bytes | str,
        *,
        reliable: bool = True,
        destination_identities: list[str] | None = None,
        topic: str = "",
    ) -> None:
        """Keep the packet."""
        packed = payload if isinstance(payload, bytes) else payload.encode()
        self.published.append((topic, packed, list(destination_identities or [])))

    @override
    async def publish_dtmf(self, *, code: int, digit: str) -> None:
        """Keep the tone."""
        self.tones.append(digit)


class Room(rtc.Room):
    """A room that never connects, whose seats a test sets."""

    def __init__(self, name: str, *seats: rtc.RemoteParticipant) -> None:
        """The room and who is in it."""
        super().__init__()
        self.called = name
        self.seats = {seat.identity: seat for seat in seats}
        self.me = Seated()

    @property
    @override
    def name(self) -> str:
        """The call's name, which a room is."""
        return self.called

    @property
    @override
    def remote_participants(self) -> Mapping[str, rtc.RemoteParticipant]:
        """Whoever the test seated."""
        return self.seats

    @property
    @override
    def local_participant(self) -> rtc.LocalParticipant:
        """The agent's seat."""
        return self.me


def _copied[Info: (SIPInboundTrunkInfo, SIPDispatchRuleInfo)](info: Info) -> Info:
    kept = type(info)()
    kept.CopyFrom(info)
    return kept


class Sip(SipService):
    """The server's SIP door, answering as a test says and keeping what it was asked."""

    requests: list[object]
    answer: TransferSIPParticipantResponse
    refusal: api.TwirpError | None
    # The SFU's inbound trunks and dispatch rules, by id, as Redis would hold them.
    trunks: dict[str, SIPInboundTrunkInfo]
    rules: dict[str, SIPDispatchRuleInfo]

    @override
    async def list_inbound_trunk(
        self, list: ListSIPInboundTrunkRequest
    ) -> ListSIPInboundTrunkResponse:
        """Every trunk, or those listing one of the numbers asked."""
        self.requests.append(list)
        wanted = set(list.numbers)
        items = [
            value for value in self.trunks.values() if not wanted or wanted & set(value.numbers)
        ]
        return ListSIPInboundTrunkResponse(items=items)

    @override
    async def create_inbound_trunk(
        self, create: CreateSIPInboundTrunkRequest
    ) -> SIPInboundTrunkInfo:
        """A trunk with an id of its own."""
        self.requests.append(create)
        made = SIPInboundTrunkInfo()
        made.CopyFrom(create.trunk)
        made.sip_trunk_id = f"ST_{len(self.trunks) + 1}"
        self.trunks[made.sip_trunk_id] = made
        return _copied(made)

    @override
    async def update_inbound_trunk(
        self, trunk_id: str, trunk: SIPInboundTrunkInfo
    ) -> SIPInboundTrunkInfo:
        """The trunk replaced whole."""
        self.requests.append(trunk)
        kept = _copied(trunk)
        kept.sip_trunk_id = trunk_id
        self.trunks[trunk_id] = kept
        return _copied(kept)

    @override
    async def update_inbound_trunk_fields(
        self,
        trunk_id: str,
        *,
        numbers: ListUpdate | list[str] | None = None,
        allowed_addresses: ListUpdate | list[str] | None = None,
        allowed_numbers: ListUpdate | list[str] | None = None,
        auth_username: str | None = None,
        auth_password: str | None = None,
        name: str | None = None,
        metadata: str | None = None,
        media: SIPMediaConfig | None = None,
    ) -> SIPInboundTrunkInfo:
        """The trunk's numbers replaced, the one field this runtime updates alone."""
        self.requests.append(("numbers", trunk_id, numbers))
        kept = self.trunks[trunk_id]
        if isinstance(numbers, list):
            kept.numbers[:] = numbers
        return _copied(kept)

    @override
    async def list_dispatch_rule(
        self, list: ListSIPDispatchRuleRequest
    ) -> ListSIPDispatchRuleResponse:
        """Every rule."""
        self.requests.append(list)
        return ListSIPDispatchRuleResponse(items=[_copied(value) for value in self.rules.values()])

    @override
    async def create_dispatch_rule(
        self, create: CreateSIPDispatchRuleRequest
    ) -> SIPDispatchRuleInfo:
        """A rule, refused as livekit does when another rule of a trunk lists one of its numbers."""
        self.requests.append(create)
        made = _copied(create.dispatch_rule)
        self._refuse_an_overlap(made)
        made.sip_dispatch_rule_id = f"SDR_{len(self.rules) + 1}"
        self.rules[made.sip_dispatch_rule_id] = made
        return _copied(made)

    @override
    async def update_dispatch_rule(
        self, rule_id: str, rule: SIPDispatchRuleInfo
    ) -> SIPDispatchRuleInfo:
        """The rule replaced whole."""
        self.requests.append(rule)
        kept = _copied(rule)
        kept.sip_dispatch_rule_id = rule_id
        self._refuse_an_overlap(kept)
        self.rules[rule_id] = kept
        return _copied(kept)

    @override
    async def delete_trunk(self, delete: DeleteSIPTrunkRequest) -> SIPTrunkInfo:
        """The trunk gone."""
        self.requests.append(delete)
        gone = self.trunks.pop(delete.sip_trunk_id)
        return SIPTrunkInfo(sip_trunk_id=gone.sip_trunk_id, name=gone.name)

    @override
    async def delete_dispatch_rule(
        self, delete: DeleteSIPDispatchRuleRequest
    ) -> SIPDispatchRuleInfo:
        """The rule gone."""
        self.requests.append(delete)
        return self.rules.pop(delete.sip_dispatch_rule_id)

    def _refuse_an_overlap(self, rule: SIPDispatchRuleInfo) -> None:
        for other in self.rules.values():
            if other.sip_dispatch_rule_id == rule.sip_dispatch_rule_id:
                continue
            shared = set(other.trunk_ids) & set(rule.trunk_ids)
            numbers = set(other.numbers) & set(rule.numbers)
            everything = not other.numbers or not rule.numbers
            if shared and (numbers or everything):
                raise api.TwirpError("invalid_argument", "dispatch rule already exists", status=400)

    @override
    async def transfer_sip_participant(
        self, transfer: TransferSIPParticipantRequest, *, timeout: float | None = None
    ) -> TransferSIPParticipantResponse:
        """REFER the leg, or refuse."""
        self.requests.append(transfer)
        if self.refusal is not None:
            raise self.refusal
        return self.answer

    @override
    async def create_sip_participant(
        self,
        create: CreateSIPParticipantRequest,
        *,
        timeout: float | None = None,
        trunk_id: str | None = None,
        outbound_trunk_config: SIPOutboundConfig | None = None,
    ) -> SIPParticipantInfo:
        """Dial the leg, or refuse."""
        self.requests.append(create)
        if self.refusal is not None:
            raise self.refusal
        return SIPParticipantInfo(participant_identity=create.participant_identity)


class Rooms(RoomService):
    """The server's room door, keeping what it was asked and the rooms a test says stand."""

    requests: list[object]
    # Each standing room and whether an agent is in it.
    existing: dict[str, bool]
    # The rooms a caller still sits in.
    people: set[str]
    # Anyone else a test seats in a room: a leg dialled from it.
    seated: dict[str, list[api.ParticipantInfo]]

    @override
    async def list_rooms(self, list: ListRoomsRequest) -> ListRoomsResponse:
        """The rooms asked for that stand."""
        self.requests.append(list)
        return ListRoomsResponse(
            rooms=[api.Room(name=name) for name in list.names if name in self.existing]
        )

    @override
    async def list_participants(self, list: ListParticipantsRequest) -> ListParticipantsResponse:
        """An agent in the room if the test says so, its caller if one is left, and the rest."""
        self.requests.append(list)
        agent = api.ParticipantInfo(identity="agent", kind=api.ParticipantInfo.Kind.AGENT)
        caller = api.ParticipantInfo(identity="sip_caller", kind=api.ParticipantInfo.Kind.SIP)
        seated = [agent] if self.existing.get(list.room) else []
        people = [caller] if list.room in self.people else []
        return ListParticipantsResponse(
            participants=[*seated, *people, *self.seated.get(list.room, [])]
        )

    @override
    async def delete_room(self, delete: DeleteRoomRequest) -> DeleteRoomResponse:
        """The room goes, whoever was in it."""
        self.requests.append(delete)
        self.existing.pop(delete.room, None)
        self.people.discard(delete.room)
        self.seated.pop(delete.room, None)
        return DeleteRoomResponse()

    @override
    async def mute_published_track(self, update: MuteRoomTrackRequest) -> MuteRoomTrackResponse:
        """Mute the track."""
        self.requests.append(update)
        return MuteRoomTrackResponse()

    @override
    async def remove_participant(
        self, remove: RoomParticipantIdentity
    ) -> RemoveParticipantResponse:
        """Put the seat out."""
        self.requests.append(remove)
        return RemoveParticipantResponse()


class Dispatcher(AgentDispatchService):
    """The server's dispatch door, keeping every dispatch, or refusing as told."""

    made: list[CreateAgentDispatchRequest]
    # What the SIP rule or a visitor's token put on a room before any test's dispatch.
    seeded: list[AgentDispatch]
    refusal: api.TwirpError | None

    @override
    async def create_dispatch(self, req: CreateAgentDispatchRequest) -> AgentDispatch:
        """Send the fleet into the room, or refuse."""
        if self.refusal is not None:
            raise self.refusal
        self.made.append(req)
        return AgentDispatch(room=req.room, agent_name=req.agent_name, metadata=req.metadata)

    @override
    async def list_dispatch(self, room_name: str) -> list[AgentDispatch]:
        """The room's dispatches, the ones a test seeded first, each newer than the one before."""
        made = [
            AgentDispatch(room=req.room, agent_name=req.agent_name, metadata=req.metadata)
            for req in self.made
        ]
        found = [dispatch for dispatch in [*self.seeded, *made] if dispatch.room == room_name]
        for order, dispatch in enumerate(found, start=1):
            dispatch.state.created_at = order
        return found


class Server(api.LiveKitAPI):
    """livekit's server client, its three doors a test's."""

    def __init__(self) -> None:
        """Doors that answer yes until told otherwise."""
        super().__init__(url="http://127.0.0.1:9", api_key="key", api_secret=A_SECRET)
        self.dialled = Sip.__new__(Sip)
        self.dialled.requests, self.dialled.refusal = [], None
        self.dialled.trunks, self.dialled.rules = {}, {}
        self.dialled.answer = TransferSIPParticipantResponse(
            status=SIPTransferStatus.STS_TRANSFER_SUCCESSFUL
        )
        self.rooms = Rooms.__new__(Rooms)
        self.rooms.requests, self.rooms.existing, self.rooms.people = [], {}, set()
        self.rooms.seated = {}
        self.dispatcher = Dispatcher.__new__(Dispatcher)
        self.dispatcher.made, self.dispatcher.refusal = [], None
        self.dispatcher.seeded = []

    @property
    @override
    def sip(self) -> SipService:
        """The SIP door."""
        return self.dialled

    @property
    @override
    def room(self) -> RoomService:
        """The room door."""
        return self.rooms

    @property
    @override
    def agent_dispatch(self) -> AgentDispatchService:
        """The dispatch door."""
        return self.dispatcher


def per_world() -> dict[Env, Server]:
    """A LiveKit of each world's own, as the cluster runs them: what one holds, the other lacks."""
    return {PRODUCTION: Server(), SANDBOX: Server()}


class Player(BackgroundAudioPlayer):
    """A background player that never publishes, and keeps what it was told to play."""

    def __init__(self) -> None:
        """A player that played nothing."""
        super().__init__()
        self.played: list[tuple[str, bool]] = []
        self.handles: list[PlayHandle] = []

    @override
    def play(
        self, audio: AudioSource | AudioConfig | list[AudioConfig], *, loop: bool = False
    ) -> PlayHandle:
        """Keep the clip and whether it loops."""
        clip = audio.source if isinstance(audio, AudioConfig) else audio
        self.played.append((str(clip), loop))
        handle = PlayHandle()
        self.handles.append(handle)
        return handle


def tone(hertz: float, seconds: float, rate: int = 48000) -> list[rtc.AudioFrame]:
    """A tone cut in the 20 ms frames a room delivers: what a caller or an agent says."""
    samples = rate // 50
    t = np.arange(int(rate * seconds)) / rate
    pcm = (8000 * np.sin(2 * np.pi * hertz * t)).astype(np.int16)
    return [
        rtc.AudioFrame(pcm[i : i + samples].tobytes(), rate, 1, samples)
        for i in range(0, len(pcm), samples)
    ]


class Microphone(AudioInput):
    """The caller's leg: its frames, one every 20 ms as a room delivers them, then nothing."""

    def __init__(self, frames: list[rtc.AudioFrame]) -> None:
        """A leg that will say these frames."""
        super().__init__(label="microphone")
        self.frames = list(frames)

    @override
    async def __anext__(self) -> rtc.AudioFrame:
        """The next frame, in its own time; the end once all were said."""
        if not self.frames:
            raise StopAsyncIteration
        await asyncio.sleep(self.frames[0].duration)
        return self.frames.pop(0)


class Speaker(AudioOutput):
    """The caller's ear: it takes the agent's audio and plays it until the test says, or a cut."""

    def __init__(self) -> None:
        """An ear that heard nothing."""
        super().__init__(label="speaker", capabilities=AudioOutputCapabilities(pause=False))
        self.frames = 0
        self.heard = 0.0
        self.playing = False
        self.cut = 0

    @override
    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        """Keep the frame; the first of a segment starts it playing."""
        await super().capture_frame(frame)
        if not self.playing:
            self.playing = True
            self.on_playback_started(created_at=time.time())
        self.frames += 1
        self.heard += frame.duration

    @override
    def flush(self) -> None:
        """The segment is whole; it still plays until `played` or a cut."""
        super().flush()

    @override
    def clear_buffer(self) -> None:
        """The caller cut in: what was playing stops where it was."""
        if not self.playing:
            return
        self.playing = False
        self.cut += 1
        self.on_playback_finished(playback_position=self.heard / 2, interrupted=True)

    def played(self) -> None:
        """The segment played to its end."""
        if not self.playing:
            return
        self.playing = False
        self.on_playback_finished(playback_position=self.heard, interrupted=False)


class Speaking(rtc.RemoteParticipant):
    """A seat whose tracks a test publishes."""

    def __init__(self, seated: rtc.RemoteParticipant, *tracks: rtc.RemoteTrackPublication) -> None:
        """The seat, with its tracks."""
        owned = participant_pb2.OwnedParticipant(
            handle=handle_pb2.FfiOwnedHandle(id=0),
            info=participant_pb2.ParticipantInfo(sid=seated.sid, identity=seated.identity),
        )
        super().__init__(owned)
        self.tracks = {track.sid: track for track in tracks}

    @property
    @override
    def track_publications(self) -> Mapping[str, rtc.RemoteTrackPublication]:
        """The tracks the test published."""
        return self.tracks


def microphone(identity: str) -> rtc.RemoteParticipant:
    """A seat with a microphone published, as a caller's is."""
    info = track_pb2.TrackPublicationInfo(
        sid=f"TR_{identity}",
        name="mic",
        kind=track_pb2.KIND_AUDIO,
        source=track_pb2.SOURCE_MICROPHONE,
    )
    owned = track_pb2.OwnedTrackPublication(handle=handle_pb2.FfiOwnedHandle(id=0), info=info)
    return Speaking(seat(identity), rtc.RemoteTrackPublication(owned))


def acme_plugin() -> types.ModuleType:
    """The module of a vendor nobody ships: what `livekit.plugins.acme` would be."""
    module = types.ModuleType(f"livekit.plugins.{ACME}")
    module.__dict__.update(
        TTS=AcmeTTS, STT=AcmeSTT, LLM=AcmeLLM, OtherSTT=AcmeSTT, StreamedTTS=AcmeStreamedTTS
    )
    return module

"""What the suites fake, and only that: livekit plugins, mail, an IdP, Twilio, Meta, an embedder."""

import base64
import json
import smtplib
import time
import types
from array import array
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import Message
from typing import ClassVar, Never, Self, override

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from livekit import api, rtc
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    llm,
    stt,
    tts,
    utils,
)
from livekit.agents.llm import ChatContext, Tool, ToolChoice
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer
from livekit.agents.voice.background_audio import (
    AudioConfig,
    AudioSource,
    BackgroundAudioPlayer,
    PlayHandle,
)
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

from pinecall.domain.types import JsonObject
from pinecall.providers.build import a_mapping

ACME = "acme"
# Long enough for the JWT livekit signs with it; a secret of nothing.
A_SECRET = "a secret of thirty-two bytes or more"
A_RATE = 24_000


@dataclass
class AcmeContext:
    """A nested option, as some plugins take a context inside their options."""

    terms: list[str] = field(default_factory=list[str])


@dataclass
class AcmeOptions:
    """Every option inside one object, as some plugins take them."""

    sensitivity: float = 0.5
    context: AcmeContext | None = None


@dataclass
class AcmeVoice:
    """A voice as a plugin that lists them returns it."""

    id: str
    name: str
    category: str


class AcmeTTS(tts.TTS[Never]):
    """A voice that keeps what it was built with, spells its arguments its own way, and speaks."""

    def __init__(
        self,
        *,
        speech_key: str,
        voice_name: str | None = None,
        language_code: str | None = None,
        model: str = "acme-voice",
        silent: bool = False,
        voices: list[dict[str, str]] | None = None,
        refusal: str | None = None,
    ) -> None:
        """Keep the arguments and the audio it will speak."""
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=A_RATE,
            num_channels=1,
        )
        self.given = {
            "speech_key": speech_key,
            "voice_name": voice_name,
            "language_code": language_code,
            "model": model,
        }
        self.audio = b"" if silent else b"\x01\x00" * 480
        self.listed = voices or []
        # "status": the vendor answers 402; "connection": it never answers.
        self.refusal = refusal

    @override
    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> tts.ChunkedStream:
        """Speak the text in one piece."""
        return _Spoken(self, text, conn_options)

    async def list_voices(self) -> list[object]:
        """The voices it was told to list; a row with a category comes back as a dataclass."""
        self.refuse()
        return [AcmeVoice(**row) if "category" in row else row for row in self.listed]

    def refuse(self) -> None:
        """Raise the refusal it was built with, if any."""
        if self.refusal == "status":
            raise APIStatusError("no credit", status_code=402)
        if self.refusal == "connection":
            raise APIConnectionError("no answer")


class _Spoken(tts.ChunkedStream):
    def __init__(self, speech: AcmeTTS, text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=speech, input_text=text, conn_options=conn_options)  # pyright: ignore[reportUnknownMemberType]
        self.speech = speech
        self.audio = speech.audio

    @override
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        self.speech.refuse()
        audio = self.audio
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=A_RATE,
            num_channels=1,
            mime_type="audio/pcm",
        )
        if audio:
            output_emitter.push(audio)
        output_emitter.flush()


class AcmeStreamedTTS(tts.StreamAdapter):
    """The same voice, streaming: livekit's adapter over it, sentence by sentence."""

    def __init__(self, *, speech_key: str, model: str = "acme-voice", silent: bool = False) -> None:
        """The voice, wrapped."""
        super().__init__(tts=AcmeTTS(speech_key=speech_key, model=model, silent=silent))  # pyright: ignore[reportUnknownMemberType]


class AcmeSTT(stt.STT[Never]):
    """Ears that keep what they were built with."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "acme-ears",
        language: str | None = None,
        language_hints: list[str] | None = None,
        eot_threshold: float | None = None,
        params: AcmeOptions | None = None,
        keyterms: bool = False,
    ) -> None:
        """Keep the arguments; `keyterms` says whether it takes livekit's keyterms."""
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True, interim_results=True, keyterms=keyterms
            )
        )
        self.given: dict[str, object] = {
            "api_key": api_key,
            "model": model,
            "language": language,
            "language_hints": language_hints,
            "eot_threshold": eot_threshold,
            "params": params,
        }

    @override
    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        raise NotImplementedError


@dataclass(frozen=True)
class Called:
    """A tool call a scripted model makes."""

    name: str
    arguments: Mapping[str, object] = field(default_factory=dict[str, object])
    call_id: str = "call_1"


# One reply of a scripted model: the pieces of text it streams and the tools it calls.
type Reply = tuple[str | Called, ...]


@dataclass
class Asked:
    """One request a scripted model was sent: the context, the tools, and the tool choice."""

    items: list[llm.ChatItem]
    tools: list[str]
    tool_choice: object


class AcmeLLM(llm.LLM[Never]):
    """A model that keeps what it was built with and answers each request with its script."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "acme-1",
        temperature: float = 1.0,
        replies: list[list[str | dict[str, object]]] | None = None,
    ) -> None:
        """Keep the arguments; `replies` in order, each text pieces and tool calls."""
        super().__init__()
        self.given: dict[str, object] = {
            "api_key": api_key,
            "model": model,
            "temperature": temperature,
        }
        self.replies: list[Reply] = [tuple(_part(one) for one in reply) for reply in replies or []]
        self.asked: list[Asked] = []

    @override
    def chat(
        self,
        *,
        chat_ctx: ChatContext,
        tools: list[Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, object]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        """Stream the next reply of the script; past its end, say nothing."""
        offered = [str(getattr(tool, "id", "")) for tool in tools or []]
        self.asked.append(Asked(list(chat_ctx.items), offered, tool_choice))
        reply = self.replies.pop(0) if self.replies else ()
        return _Streamed(self, reply, chat_ctx, tools or [], conn_options)


def _part(one: str | dict[str, object]) -> str | Called:
    if isinstance(one, str):
        return one
    arguments = one.get("arguments")
    return Called(
        name=str(one["name"]),
        arguments=arguments if a_mapping(arguments) else {},
        call_id=str(one.get("call_id", "call_1")),
    )


class _Streamed(llm.LLMStream):
    def __init__(
        self,
        model: AcmeLLM,
        reply: Reply,
        chat_ctx: ChatContext,
        tools: list[Tool],
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(model, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)  # pyright: ignore[reportUnknownMemberType]
        self.reply = reply

    @override
    async def _run(self) -> None:
        for part in self.reply:
            if isinstance(part, str):
                delta = llm.ChoiceDelta(role="assistant", content=part)
            else:
                called = llm.FunctionToolCall(
                    name=part.name, arguments=json.dumps(part.arguments), call_id=part.call_id
                )
                delta = llm.ChoiceDelta(role="assistant", tool_calls=[called])
            self._event_ch.send_nowait(llm.ChatChunk(id="scripted", delta=delta))
        used = llm.CompletionUsage(completion_tokens=5, prompt_tokens=20, total_tokens=25)
        self._event_ch.send_nowait(llm.ChatChunk(id="scripted", usage=used))


def seat(
    identity: str,
    *,
    kind: int = rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
    attributes: Mapping[str, str] | None = None,
    reason: int | None = None,
) -> rtc.RemoteParticipant:
    """A seat in a room, as livekit hands one to its listeners, built without a server."""
    info = participant_pb2.ParticipantInfo(
        sid=f"PA_{identity}",
        identity=identity,
        kind=kind,  # pyright: ignore[reportArgumentType]
        attributes=dict(attributes or {}),
    )
    if reason is not None:
        info.disconnect_reason = reason  # pyright: ignore[reportAttributeAccessIssue]
    owned = participant_pb2.OwnedParticipant(handle=handle_pb2.FfiOwnedHandle(id=0), info=info)
    return rtc.RemoteParticipant(owned)


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
        self.seats = {one.identity: one for one in seats}
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


class Sip(SipService):
    """The server's SIP door, answering as a test says and keeping what it was asked."""

    asked: list[object]
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
        self.asked.append(list)
        wanted = set(list.numbers)
        items = [one for one in self.trunks.values() if not wanted or wanted & set(one.numbers)]
        return ListSIPInboundTrunkResponse(items=items)

    @override
    async def create_inbound_trunk(
        self, create: CreateSIPInboundTrunkRequest
    ) -> SIPInboundTrunkInfo:
        """A trunk with an id of its own."""
        self.asked.append(create)
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
        self.asked.append(trunk)
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
        self.asked.append(("numbers", trunk_id, numbers))
        kept = self.trunks[trunk_id]
        if isinstance(numbers, list):
            kept.numbers[:] = numbers
        return _copied(kept)

    @override
    async def list_dispatch_rule(
        self, list: ListSIPDispatchRuleRequest
    ) -> ListSIPDispatchRuleResponse:
        """Every rule."""
        self.asked.append(list)
        return ListSIPDispatchRuleResponse(items=[_copied(one) for one in self.rules.values()])

    @override
    async def create_dispatch_rule(
        self, create: CreateSIPDispatchRuleRequest
    ) -> SIPDispatchRuleInfo:
        """A rule, refused as livekit does when another rule of a trunk lists one of its numbers."""
        self.asked.append(create)
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
        self.asked.append(rule)
        kept = _copied(rule)
        kept.sip_dispatch_rule_id = rule_id
        self._refuse_an_overlap(kept)
        self.rules[rule_id] = kept
        return _copied(kept)

    @override
    async def delete_trunk(self, delete: DeleteSIPTrunkRequest) -> SIPTrunkInfo:
        """The trunk gone."""
        self.asked.append(delete)
        gone = self.trunks.pop(delete.sip_trunk_id)
        return SIPTrunkInfo(sip_trunk_id=gone.sip_trunk_id, name=gone.name)

    @override
    async def delete_dispatch_rule(
        self, delete: DeleteSIPDispatchRuleRequest
    ) -> SIPDispatchRuleInfo:
        """The rule gone."""
        self.asked.append(delete)
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
        self.asked.append(transfer)
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
        self.asked.append(create)
        if self.refusal is not None:
            raise self.refusal
        return SIPParticipantInfo(participant_identity=create.participant_identity)


class Rooms(RoomService):
    """The server's room door, keeping what it was asked and the rooms a test says stand."""

    asked: list[object]
    # Each standing room and whether an agent is in it.
    standing: dict[str, bool]

    @override
    async def list_rooms(self, list: ListRoomsRequest) -> ListRoomsResponse:
        """The rooms asked for that stand."""
        self.asked.append(list)
        return ListRoomsResponse(
            rooms=[api.Room(name=name) for name in list.names if name in self.standing]
        )

    @override
    async def list_participants(self, list: ListParticipantsRequest) -> ListParticipantsResponse:
        """An agent in the room, or nobody."""
        agent = api.ParticipantInfo(identity="agent", kind=api.ParticipantInfo.Kind.AGENT)
        return ListParticipantsResponse(
            participants=[agent] if self.standing.get(list.room) else []
        )

    @override
    async def delete_room(self, delete: DeleteRoomRequest) -> DeleteRoomResponse:
        """The room goes, whoever was in it."""
        self.asked.append(delete)
        self.standing.pop(delete.room, None)
        return DeleteRoomResponse()

    @override
    async def mute_published_track(self, update: MuteRoomTrackRequest) -> MuteRoomTrackResponse:
        """Mute the track."""
        self.asked.append(update)
        return MuteRoomTrackResponse()

    @override
    async def remove_participant(
        self, remove: RoomParticipantIdentity
    ) -> RemoveParticipantResponse:
        """Put the seat out."""
        self.asked.append(remove)
        return RemoveParticipantResponse()


class Dispatcher(AgentDispatchService):
    """The server's dispatch door, keeping every dispatch, or refusing as told."""

    made: list[CreateAgentDispatchRequest]
    refusal: api.TwirpError | None

    @override
    async def create_dispatch(self, req: CreateAgentDispatchRequest) -> AgentDispatch:
        """Send the fleet into the room, or refuse."""
        if self.refusal is not None:
            raise self.refusal
        self.made.append(req)
        return AgentDispatch(room=req.room, agent_name=req.agent_name, metadata=req.metadata)


class Server(api.LiveKitAPI):
    """livekit's server client, its three doors a test's."""

    def __init__(self) -> None:
        """Doors that answer yes until told otherwise."""
        super().__init__(url="http://127.0.0.1:9", api_key="key", api_secret=A_SECRET)
        self.dialled = Sip.__new__(Sip)
        self.dialled.asked, self.dialled.refusal = [], None
        self.dialled.trunks, self.dialled.rules = {}, {}
        self.dialled.answer = TransferSIPParticipantResponse(
            status=SIPTransferStatus.STS_TRANSFER_SUCCESSFUL
        )
        self.rooms = Rooms.__new__(Rooms)
        self.rooms.asked, self.rooms.standing = [], {}
        self.dispatcher = Dispatcher.__new__(Dispatcher)
        self.dispatcher.made, self.dispatcher.refusal = [], None

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


# ── a mail server ──


@dataclass
class Postbox:
    """What a fake mail server was told: who signed in, what was sent, and what it refuses."""

    hosts: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    logins: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    starttls: int = 0
    sent: list[Message] = field(default_factory=list[Message])
    # A reply code and sentence the server answers the login or the letter with.
    refuses_login: tuple[int, str] | None = None
    refuses_letter: tuple[int, str] | None = None


# Put in smtplib's place by the test, with the postbox it answers from.
class MailServer(smtplib.SMTP):
    """A mail server that answers as its postbox says, and keeps what it was sent."""

    postbox: ClassVar[Postbox] = Postbox()

    @override
    def __init__(self, host: str = "", port: int = 0, **_: object) -> None:
        self.postbox.hosts.append((host, port))

    @override
    def __enter__(self) -> Self:
        return self

    @override
    def __exit__(self, *_: object) -> None:
        return

    @override
    def starttls(self, *_: object, **__: object) -> tuple[int, bytes]:
        self.postbox.starttls += 1
        return 220, b"ready"

    @override
    def login(
        self, user: str, password: str, *, initial_response_ok: bool = True
    ) -> tuple[int, bytes]:
        if self.postbox.refuses_login is not None:
            code, said = self.postbox.refuses_login
            raise smtplib.SMTPAuthenticationError(code, said.encode())
        self.postbox.logins.append((user, password))
        return 235, b"ok"

    @override
    def send_message(self, msg: Message, *_: object, **__: object) -> dict[str, tuple[int, bytes]]:
        if self.postbox.refuses_letter is not None:
            code, said = self.postbox.refuses_letter
            raise smtplib.SMTPDataError(code, said.encode())
        self.postbox.sent.append(msg)
        return {}


# ── an identity provider ──


# One RSA key for the whole suite: making one costs a second.
_IDP_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@dataclass
class IdentityProvider:
    """An OpenID provider on a fake transport: discovery, the token endpoint and its JWKS."""

    issuer: str = "https://idp.test"
    client_id: str = "the-client"
    # What the token endpoint hands back for a code; None refuses the exchange.
    id_token: str | None = None
    # How the token endpoint answers a refused code.
    refusal: tuple[int, str] = (400, '{"error": "invalid_grant"}')
    kid: str | None = "k1"
    basic_only: bool = False
    exchanged: list[dict[str, str]] = field(default_factory=list[dict[str, str]])

    def signed(self, *, nonce: str, email: str = "ana@clinica.test", **claims: object) -> str:
        """An id_token this provider signed, with the claims a sign-in needs and any others."""
        now = int(time.time())
        said: dict[str, object] = {
            "iss": self.issuer,
            "aud": self.client_id,
            "sub": "sub-ana",
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
            "email": email,
            "email_verified": True,
            "name": "Ana García",
            **claims,
        }
        headers = {} if self.kid is None else {"kid": self.kid}
        pem = _IDP_KEY.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return jwt.encode(said, pem, algorithm="RS256", headers=headers)

    def transport(self) -> httpx.MockTransport:
        """A transport that answers as this provider does."""
        return httpx.MockTransport(self._answer)

    def _answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            methods = ["client_secret_basic"] if self.basic_only else ["client_secret_post"]
            return httpx.Response(
                200,
                json={
                    "issuer": self.issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize?prompt=select_account",
                    "token_endpoint": f"{self.issuer}/token",
                    "jwks_uri": f"{self.issuer}/jwks",
                    "token_endpoint_auth_methods_supported": methods,
                },
            )
        if path == "/jwks":
            key: dict[str, object] = RSAAlgorithm.to_jwk(_IDP_KEY.public_key(), as_dict=True)
            if self.kid is not None:
                key["kid"] = self.kid
            return httpx.Response(200, json={"keys": [key]})
        if path == "/token":
            self.exchanged.append(dict(httpx.QueryParams(request.content.decode()).items()))
            if self.id_token is None:
                status, body = self.refusal
                return httpx.Response(status, content=body)
            return httpx.Response(200, json={"id_token": self.id_token})
        return httpx.Response(404)


def _copied[Info: (SIPInboundTrunkInfo, SIPDispatchRuleInfo)](info: Info) -> Info:
    kept = type(info)()
    kept.CopyFrom(info)
    return kept


# A SID made here, never one of Twilio's: two letters and 32 hex digits.
def a_sid(prefix: str, seed: int) -> str:
    """A SID of that kind, the same one for the same seed."""
    return f"{prefix}{seed:032x}"


@dataclass
class TwilioTrunkHeld:
    """One trunk of the fake account."""

    sid: str
    friendly_name: str
    domain_name: str | None = None
    origination: list[str] = field(default_factory=list[str])
    credential_lists: list[str] = field(default_factory=list[str])


type Row = dict[str, str | None]


@dataclass
class Twilio:
    """One Twilio account on a fake transport: numbers, trunks, credential lists, a shop."""

    account_sid: str = field(default_factory=lambda: a_sid("AC", 1))
    user: str = field(default_factory=lambda: a_sid("SK", 1))
    secret: str = "the key's secret"
    # Number -> (its SID, the trunk it is attached to).
    numbers: dict[str, tuple[str, str | None]] = field(
        default_factory=dict[str, tuple[str, str | None]]
    )
    trunks: dict[str, TwilioTrunkHeld] = field(default_factory=dict[str, TwilioTrunkHeld])
    # SID -> (its name, its credentials).
    credential_lists: dict[str, tuple[str, list[tuple[str, str]]]] = field(
        default_factory=dict[str, tuple[str, list[tuple[str, str]]]]
    )
    for_sale: list[str] = field(default_factory=list[str])
    # A listing longer than this comes in pages.
    page_size: int = 50
    asked: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def owns(self, number: str, *, trunk: str | None = None) -> str:
        """Give the account a number, attached to a trunk or to none; its SID."""
        sid = a_sid("PN", len(self.numbers) + 1)
        self.numbers[number] = (sid, trunk)
        return sid

    def trunk(self, name: str, *origination: str) -> str:
        """Give the account a trunk sending its calls to these URIs; its SID."""
        sid = a_sid("TK", len(self.trunks) + 1)
        held = TwilioTrunkHeld(sid=sid, friendly_name=name, origination=list(origination))
        self.trunks[sid] = held
        return sid

    def transport(self) -> httpx.MockTransport:
        """A transport that answers as the account does."""
        return httpx.MockTransport(self._answer)

    def written(self) -> list[tuple[str, str]]:
        """Every request that changed something, in order."""
        return [one for one in self.asked if one[0] != "GET"]

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.asked.append((request.method, request.url.path))
        pair = httpx.BasicAuth(self.user, self.secret)
        if request.headers.get("authorization") != next(pair.auth_flow(request)).headers.get(
            "authorization"
        ):
            return httpx.Response(401, json={"code": 20003, "message": "Authenticate"})
        form = dict(httpx.QueryParams(request.content.decode()).items())
        if request.url.host == "trunking.twilio.com":
            return self._trunking(request, request.url.path.removeprefix("/v1"), form)
        return self._accounts(request, form)

    def _accounts(self, request: httpx.Request, form: dict[str, str]) -> httpx.Response:
        path = request.url.path.removeprefix(f"/2010-04-01/Accounts/{self.account_sid}")
        if path == ".json":
            return httpx.Response(200, json={"sid": self.account_sid, "friendly_name": "Clinica"})
        if path == "/IncomingPhoneNumbers.json":
            return self._numbers(request, form)
        if path.startswith("/AvailablePhoneNumbers/"):
            shown = [{"phone_number": one} for one in self.for_sale[:1]]
            return httpx.Response(200, json={"available_phone_numbers": shown})
        if path.startswith("/SIP/CredentialLists"):
            return self._lists(request, path, form)
        return httpx.Response(404, json={"message": f"no {path}"})

    def _numbers(self, request: httpx.Request, form: dict[str, str]) -> httpx.Response:
        if request.method == "POST":
            bought = form["PhoneNumber"]
            self.for_sale.remove(bought)
            return httpx.Response(201, json=_number_row(bought, self.owns(bought), None))
        asked = request.url.params.get("PhoneNumber")
        rows = [_number_row(one, sid, trunk) for one, (sid, trunk) in self.numbers.items()]
        kept = [one for one in rows if asked is None or one["phone_number"] == asked]
        return self._paged(request, "incoming_phone_numbers", kept, by_uri=True)

    def _lists(self, request: httpx.Request, path: str, form: dict[str, str]) -> httpx.Response:
        if path != "/SIP/CredentialLists.json":
            credentials = self.credential_lists[path.split("/")[3]][1]
            if request.method == "POST":
                credentials.append((form["Username"], form["Password"]))
                return httpx.Response(201, json={"sid": a_sid("CR", len(credentials))})
            held: list[Row] = [{"username": username} for username, _ in credentials]
            return self._paged(request, "credentials", held, by_uri=True)
        if request.method == "POST":
            sid = a_sid("CL", len(self.credential_lists) + 1)
            self.credential_lists[sid] = (form["FriendlyName"], [])
            return httpx.Response(201, json={"sid": sid, "friendly_name": form["FriendlyName"]})
        rows: list[Row] = [
            {"sid": sid, "friendly_name": name} for sid, (name, _) in self.credential_lists.items()
        ]
        return self._paged(request, "credential_lists", rows, by_uri=True)

    def _trunking(self, request: httpx.Request, path: str, form: dict[str, str]) -> httpx.Response:
        parts = path.strip("/").split("/")
        if parts == ["Trunks"]:
            if request.method == "POST":
                sid = self.trunk(form["FriendlyName"])
                return httpx.Response(201, json=_trunk_row(self.trunks[sid]))
            rows = [_trunk_row(one) for one in self.trunks.values()]
            return self._paged(request, "trunks", rows, by_uri=False)
        held = self.trunks[parts[1]]
        if len(parts) == 2:
            if request.method == "POST":
                if not form["DomainName"].endswith(".pstn.twilio.com"):
                    return httpx.Response(400, json={"code": 21245, "message": "Invalid domain"})
                held.domain_name = form["DomainName"]
            return httpx.Response(200, json=_trunk_row(held))
        return self._of_a_trunk(request, held, parts[2:], form)

    def _of_a_trunk(
        self, request: httpx.Request, held: TwilioTrunkHeld, parts: list[str], form: dict[str, str]
    ) -> httpx.Response:
        if parts == ["OriginationUrls"]:
            if request.method == "POST":
                held.origination.append(form["SipUrl"])
            rows = [
                {"sid": a_sid("OU", n), "sip_url": url} for n, url in enumerate(held.origination)
            ]
            return httpx.Response(200, json={"origination_urls": rows, "meta": {}})
        if parts[0] == "PhoneNumbers":
            wanted = {form.get("PhoneNumberSid"), parts[-1]}
            number = next(one for one, (sid, _) in self.numbers.items() if sid in wanted)
            sid, _ = self.numbers[number]
            detached = request.method == "DELETE"
            self.numbers[number] = (sid, None if detached else held.sid)
            return httpx.Response(204 if detached else 201)
        if request.method == "POST":
            held.credential_lists.append(form["CredentialListSid"])
        rows = [{"sid": sid} for sid in held.credential_lists]
        return httpx.Response(200, json={"credential_lists": rows, "meta": {}})

    def _paged(
        self, request: httpx.Request, key: str, rows: list[Row], *, by_uri: bool
    ) -> httpx.Response:
        page = int(request.url.params.get("Page", "0"))
        shown = rows[page * self.page_size : (page + 1) * self.page_size]
        more = (page + 1) * self.page_size < len(rows)
        following = request.url.copy_merge_params({"Page": str(page + 1)}) if more else None
        if by_uri:
            uri = None if following is None else f"{following.path}?{following.query.decode()}"
            return httpx.Response(200, json={key: shown, "next_page_uri": uri})
        url = None if following is None else str(following)
        return httpx.Response(200, json={key: shown, "meta": {"next_page_url": url}})


@dataclass
class Graph:
    """Meta's Graph API on a fake transport: every message sent, or a refusal."""

    sent: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    tokens: list[str] = field(default_factory=list[str])
    # How Graph answers a send it refuses; None sends.
    refusal: tuple[int, str] | None = None

    # The number the fake account answers at, as Meta shows it.
    number: str = "+598 29 001 199"
    name: str = "Clinica"

    def answer(self, request: httpx.Request) -> httpx.Response:
        """A send to a number's messages, kept, or refused as told; the account's number on GET."""
        if request.method == "GET":
            if self.refusal is not None:
                status, said = self.refusal
                return httpx.Response(status, json={"error": {"message": said, "code": 190}})
            said = {"display_phone_number": self.number, "verified_name": self.name}
            return httpx.Response(200, json={**said, "id": request.url.path.split("/")[2]})
        if self.refusal is not None:
            status, said = self.refusal
            return httpx.Response(status, json={"error": {"message": said, "code": 131047}})
        self.tokens.append(request.headers.get("authorization", ""))
        body: dict[str, object] = json.loads(request.content)
        self.sent.append({"from": request.url.path.split("/")[2], **body})
        return httpx.Response(200, json={"messages": [{"id": f"wamid.{len(self.sent)}"}]})


# Too big, in the words one vendor refuses a request with.
TOO_BIG = "Input total size exceeds maximum number of allowed tokens"
CONTEXTUALIZED = "/contextualizedembeddings"


@dataclass
class Embeddings:
    """An embeddings vendor on a fake transport: both wire shapes, a script of failures first."""

    width: int = 1024
    # The value every float, and every signed byte of a contextual vector, carries.
    value: float = 0.5
    byte: int = 3
    # A request with more inputs than this is refused as too big.
    too_big_over: int | None = None
    # Answered in order before any request is answered well.
    script: list[httpx.Response | httpx.TransportError] = field(
        default_factory=list[httpx.Response | httpx.TransportError]
    )
    asked: list[httpx.Request] = field(default_factory=list[httpx.Request])

    def transport(self) -> httpx.MockTransport:
        """A transport that answers as the vendor does."""
        return httpx.MockTransport(self._answer)

    def sent(self) -> list[JsonObject]:
        """Every body the vendor was sent, in order."""
        return [json.loads(request.content) for request in self.asked]

    def inputs(self) -> list[list[str]]:
        """The texts of every request, in order: a contextual one's are its one window."""
        return [_texts_of(request) for request in self.asked]

    def _answer(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if self.script:
            scripted = self.script.pop(0)
            if isinstance(scripted, httpx.TransportError):
                raise scripted
            return scripted
        texts = _texts_of(request)
        if self.too_big_over is not None and len(texts) > self.too_big_over:
            return httpx.Response(400, json={"error": {"message": TOO_BIG}})
        if request.url.path.endswith(CONTEXTUALIZED):
            vector = int8_vector([self.byte] * self.width)
            rows = [{"data": [{"embedding": vector} for _ in texts]}]
            return httpx.Response(200, json={"data": rows})
        rows = [{"index": at, "embedding": [self.value] * self.width} for at in range(len(texts))]
        return httpx.Response(200, json={"object": "list", "data": rows})


def _texts_of(request: httpx.Request) -> list[str]:
    said = json.loads(request.content)
    return said["input"][0] if request.url.path.endswith(CONTEXTUALIZED) else said["input"]


def int8_vector(values: list[int]) -> str:
    """A vector as the contextual shape sends it: signed bytes, base64."""
    return base64.b64encode(array("b", values).tobytes()).decode("ascii")


def outside(twilio: Twilio, graph: Graph) -> httpx.MockTransport:
    """One transport for everything outside the box: Meta's Graph, and Twilio for the rest."""

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.facebook.com":
            return graph.answer(request)
        return twilio.transport().handle_request(request)

    return httpx.MockTransport(answer)


def _number_row(number: str, sid: str, trunk: str | None) -> Row:
    return {"sid": sid, "phone_number": number, "friendly_name": number, "trunk_sid": trunk}


def _trunk_row(held: TwilioTrunkHeld) -> Row:
    return {"sid": held.sid, "friendly_name": held.friendly_name, "domain_name": held.domain_name}

"""What the suites fake, and only that: livekit plugins, a mail server, an identity provider."""

import json
import smtplib
import time
import types
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
from livekit.api.room_service import RoomService
from livekit.api.sip_service import SipService
from livekit.protocol.room import (
    MuteRoomTrackRequest,
    MuteRoomTrackResponse,
    RemoveParticipantResponse,
    RoomParticipantIdentity,
)
from livekit.protocol.sip import (
    CreateSIPParticipantRequest,
    SIPOutboundConfig,
    SIPParticipantInfo,
    SIPTransferStatus,
    TransferSIPParticipantRequest,
    TransferSIPParticipantResponse,
)
from livekit.rtc._proto import handle_pb2, participant_pb2, track_pb2
from livekit.rtc._utils import BroadcastQueue

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
    """The server's room door, keeping what it was asked."""

    asked: list[object]

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


class Server(api.LiveKitAPI):
    """livekit's server client, its two doors a test's."""

    def __init__(self) -> None:
        """Doors that answer yes until told otherwise."""
        super().__init__(url="http://127.0.0.1:9", api_key="key", api_secret=A_SECRET)
        self.dialled = Sip.__new__(Sip)
        self.dialled.asked, self.dialled.refusal = [], None
        self.dialled.answer = TransferSIPParticipantResponse(
            status=SIPTransferStatus.STS_TRANSFER_SUCCESSFUL
        )
        self.rooms = Rooms.__new__(Rooms)
        self.rooms.asked = []

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

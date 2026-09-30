"""The vendor `acme`: a scripted model, and ears and a voice that keep what they were built with."""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Never, override

from livekit import rtc
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
from livekit.rtc._proto import handle_pb2, participant_pb2

from pinecall.providers.build import a_mapping

ACME = "acme"

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
        channels: int = 1,
    ) -> None:
        """Keep the arguments and the audio it will speak, in `channels` channels."""
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=A_RATE,
            num_channels=channels,
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
        eot_timeout_ms: int | None = None,
        params: AcmeOptions | None = None,
        keyterms: bool = False,
        streams: bool = True,
    ) -> None:
        """Keep the arguments; `keyterms` and `streams` say what livekit may ask of it."""
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=streams, interim_results=True, keyterms=keyterms
            )
        )
        self.given: dict[str, object] = {
            "api_key": api_key,
            "model": model,
            "language": language,
            "language_hints": language_hints,
            "eot_threshold": eot_threshold,
            "eot_timeout_ms": eot_timeout_ms,
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
class ModelRequest:
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
        refusal: str | None = None,
    ) -> None:
        """Keep the arguments; `replies` in order; `refusal` a vendor that never answers."""
        super().__init__()
        self.refusal = refusal
        self.given: dict[str, object] = {
            "api_key": api_key,
            "model": model,
            "temperature": temperature,
        }
        self.replies: list[Reply] = [
            tuple(_part(item) for item in reply) for reply in replies or []
        ]
        self.requests: list[ModelRequest] = []

    @property
    @override
    def model(self) -> str:
        """The model it was built with, as a real plugin names the one it runs."""
        return str(self.given["model"])

    @property
    @override
    def provider(self) -> str:
        """The vendor, as a real plugin names itself."""
        return ACME

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
        self.requests.append(ModelRequest(list(chat_ctx.items), offered, tool_choice))
        reply = self.replies.pop(0) if self.replies else ()
        return _Streamed(self, reply, chat_ctx, tools or [], conn_options)


def _part(item: str | dict[str, object]) -> str | Called:
    if isinstance(item, str):
        return item
    arguments = item.get("arguments")
    return Called(
        name=str(item["name"]),
        arguments=arguments if a_mapping(arguments) else {},
        call_id=str(item.get("call_id", "call_1")),
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
        self.refusing = model.refusal

    @override
    async def _run(self) -> None:
        if self.refusing:
            raise APIConnectionError(self.refusing)
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

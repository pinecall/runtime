"""What a keyless session test runs on: silent vendors, a kit with nothing behind it."""

from __future__ import annotations

from typing import Any, cast, override

from livekit.agents import llm as agents
from livekit.agents import stt, tts
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions
from livekit.agents.utils import AudioBuffer

from pinecall.providers.pipeline import Pipeline
from pinecall.providers.registry import Chat
from pinecall.types import AgentConfig, ProviderKeys

# Nothing here ever opens a socket: every method a session might reach for on the way to a vendor
# raises instead, so a test that accidentally starts talking fails loudly rather than dialling out.
NEVER_SPOKEN = "a unit test has no audio and no vendor"


class SilentEars(stt.STT[Any]):
    """livekit's STT with nothing behind it: a session builds, and no audio ever leaves."""

    def __init__(self, *, keyterms: bool = False) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True, interim_results=True, keyterms=keyterms
            )
        )

    @override
    async def _recognize_impl(
        self,
        buffer: AudioBuffer,  # noqa: ARG002 — the base class's signature
        *,
        language: Any = NOT_GIVEN,  # noqa: ARG002 — the base class's signature
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,  # noqa: ARG002
    ) -> stt.SpeechEvent:
        raise NotImplementedError(NEVER_SPOKEN)


class SilentVoice(tts.TTS[Any]):
    """livekit's TTS with nothing behind it: the session has a voice it will never use."""

    def __init__(self) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=16000,
            num_channels=1,
        )

    @override
    def synthesize(
        self,
        text: str,  # noqa: ARG002 — the base class's signature
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,  # noqa: ARG002
    ) -> tts.ChunkedStream:
        raise NotImplementedError(NEVER_SPOKEN)


class FakeKit:
    """A Kit with no vendor behind it: one pipeline for every agent, and who asked for it."""

    def __init__(self, llm: Chat, *, keyterms: bool = False) -> None:
        self.built: list[str] = []
        self.keys: list[ProviderKeys] = []
        self.pipe = Pipeline(llm=llm, stt=SilentEars(keyterms=keyterms), tts=SilentVoice())

    def __call__(self, config: AgentConfig, keys: ProviderKeys) -> Pipeline:
        """The three, already built: nothing here reads a key or opens a socket."""
        self.built.append(config.slug)
        self.keys.append(keys)
        return self.pipe


# livekit's own formatter, called at the one place a test needs it: the return is generic enough
# that a strict checker cannot read it, and one ignore here is better than one per assertion.
def anthropic_request(context: agents.ChatContext) -> tuple[list[dict[str, Any]], Any]:
    """One request as the Anthropic formatter builds it: the messages, and the system blocks."""
    built = cast(
        "tuple[list[dict[str, Any]], Any]",
        context.to_provider_format("anthropic"),  # pyright: ignore[reportUnknownMemberType]
    )
    return built

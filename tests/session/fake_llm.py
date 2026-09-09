"""A livekit LLM that says exactly what a test tells it to say, and remembers what it was asked."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, override

from livekit.agents import llm
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, APIConnectOptions

# What a provider that reports nothing at all would send back: the metrics entry has to hold up
# with a usage block this empty, and a test says so by using this.
NOTHING_REPORTED = llm.CompletionUsage(completion_tokens=0, prompt_tokens=0, total_tokens=0)

# The scripted stream yields one text chunk per piece of the script, then one chunk per tool call,
# then a chunk that carries nothing but the usage — the shape a real provider streams.
_A_CHUNK = "chunk_"


@dataclass
class Scripted:
    """One request's answer: the text in the chunks it streams, and what it wants to call."""

    chunks: tuple[str, ...] = ()
    calls: tuple[llm.FunctionToolCall, ...] = ()
    usage: llm.CompletionUsage | None = None
    request_id: str = "req_fake"


def a_call(
    call_id: str, name: str, arguments: dict[str, Any] | None = None
) -> llm.FunctionToolCall:
    """One tool call as livekit carries it: the arguments are the JSON string the vendor sent."""
    return llm.FunctionToolCall(call_id=call_id, name=name, arguments=json.dumps(arguments or {}))


@dataclass
class Asked:
    """One request as the session made it, kept so a test can read the context it was given."""

    chat_ctx: llm.ChatContext
    tools: tuple[str, ...]
    # The tool objects themselves, so a test can format them the way the plugin does.
    declared: tuple[llm.Tool, ...] = ()

    @property
    def instructions(self) -> str:
        """The cached prefix: livekit's `instructions`, which is the FIRST system message."""
        for message in self.chat_ctx.messages():
            if message.role == "system":
                return message.text_content or ""
        return ""

    @property
    def system(self) -> str:
        """Every system message of the request, joined: the prompt's blocks, in the order sent."""
        said = [
            message.text_content or ""
            for message in self.chat_ctx.messages()
            if message.role == "system"
        ]
        return "\n\n".join(said)

    @property
    def history(self) -> tuple[llm.ChatMessage, ...]:
        """Everything that was said, without the prompt: the turns, in order."""
        return tuple(message for message in self.chat_ctx.messages() if message.role != "system")

    @property
    def outputs(self) -> tuple[llm.FunctionCallOutput, ...]:
        """The tool results the model was handed back, in order."""
        return tuple(
            item for item in self.chat_ctx.items if isinstance(item, llm.FunctionCallOutput)
        )

    @property
    def calls(self) -> tuple[llm.FunctionCall, ...]:
        """The tool calls the history remembers the model having made."""
        return tuple(item for item in self.chat_ctx.items if isinstance(item, llm.FunctionCall))


# The base class does the measuring: _run() pushes ChatChunks into the channel livekit owns, and
# livekit's own monitor derives the LLMMetrics from them and emits metrics_collected. Nothing here
# fakes a metric, which is the point — the entry a test reads is the library's own arithmetic.
class ScriptedStream(llm.LLMStream):
    """One scripted answer, streamed chunk by chunk the way a real one arrives."""

    def __init__(self, source: FakeLLM, said: Scripted, **passed: Any) -> None:
        super().__init__(source, **passed)  # pyright: ignore[reportUnknownMemberType]
        self._said = said

    @override
    async def _run(self) -> None:
        said = self._said
        for chunk in said.chunks:
            self._event_ch.send_nowait(
                llm.ChatChunk(
                    id=said.request_id, delta=llm.ChoiceDelta(role="assistant", content=chunk)
                )
            )
        if said.calls:
            self._event_ch.send_nowait(
                llm.ChatChunk(
                    id=said.request_id,
                    delta=llm.ChoiceDelta(role="assistant", tool_calls=list(said.calls)),
                )
            )
        if said.usage is not None:
            self._event_ch.send_nowait(llm.ChatChunk(id=said.request_id, usage=said.usage))


class FakeLLM(llm.LLM[Any]):
    """livekit's LLM, scripted: one Scripted per request, in the order they were given."""

    def __init__(self, *script: Scripted) -> None:
        super().__init__()
        self.script: list[Scripted] = list(script)
        self.asked: list[Asked] = []

    @property
    @override
    def model(self) -> str:
        return "claude-haiku-4-5-20251001"

    @property
    @override
    def provider(self) -> str:
        return "anthropic"

    @property
    def requests(self) -> int:
        """How many times the session went to the model. agent.say must never move this."""
        return len(self.asked)

    @override
    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: Any = NOT_GIVEN,  # noqa: ARG002 — the base class's signature
        tool_choice: Any = NOT_GIVEN,  # noqa: ARG002 — the base class's signature
        extra_kwargs: Any = NOT_GIVEN,  # noqa: ARG002 — the base class's signature
    ) -> ScriptedStream:
        """The next scripted answer, with the request kept exactly as the session built it."""
        # A COPY: livekit keeps mutating the one context it owns as the turn goes on, so a
        # reference here would let a later item rewrite what this request was actually given.
        self.asked.append(
            Asked(
                chat_ctx=chat_ctx.copy(),
                tools=tuple(_named(tools or [])),
                declared=tuple(tools or []),
            )
        )
        said = self.script.pop(0) if self.script else Scripted(chunks=("...",))
        return ScriptedStream(
            self, said, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options
        )


# livekit's raw-tool helpers are generic over the wrapped function's ParamSpec, which a strict
# checker can only read as Unknown. The tool's own info carries the same name as a plain string.
def _named(tools: list[llm.Tool]) -> list[str]:
    """The names of the tools the request declared, as the schema spells them."""
    told = [getattr(tool, "info", None) for tool in tools]
    return [str(info.name) for info in told if info is not None]

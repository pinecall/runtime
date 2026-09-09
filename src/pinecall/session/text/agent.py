"""livekit's Agent for a text call: the view enters per request, and the log is written from it."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from typing import Any, Protocol, cast, override

from livekit.agents import llm as agents
from livekit.agents.metrics import LLMMetrics as Measured
from livekit.agents.voice import ModelSettings
from livekit.agents.voice.agent import Agent as LiveAgent

from pinecall.providers.models import Chat


class Writer(Protocol):
    """What the agent needs of the session: the view of the moment, and a hand on the log."""

    @property
    def view(self) -> str:
        """The dynamic region as the app last rendered it; empty when there is none."""
        ...

    async def thinking(self) -> None:
        """A request is going out."""
        ...

    async def said(self, text: str) -> None:
        """One delta of the answer arrived."""
        ...

    async def measured(self, metrics: Sequence[Measured]) -> None:
        """What livekit measured of the request that just finished."""
        ...


# livekit's EventEmitter is generic over an unbounded parameter, so a strict checker cannot read
# its .on/.off; the one cast in the runtime that says what they are lives here, at the one seam
# that listens. The metrics arrive on the llm's own `metrics_collected`, once, when the stream is
# closed — nothing here computes a number.
class Metered:
    """Everything the llm measured while this was open, in the order livekit emitted it."""

    def __init__(self, llm: Chat) -> None:
        self._emitter = cast(Any, llm)
        self.seen: list[Measured] = []

    def __enter__(self) -> Metered:
        self._emitter.on("metrics_collected", self.seen.append)
        return self

    def __exit__(self, *_closed: object) -> None:
        self._emitter.off("metrics_collected", self.seen.append)


type Node = AsyncGenerator[agents.ChatChunk | str, None]


class TextAgent(LiveAgent):
    """The agent livekit runs: our instructions, our tools, and the view added at the end."""

    def __init__(
        self,
        *,
        instructions: str,
        tools: Sequence[agents.Tool],
        llm: Chat,
        writer: Writer,
    ) -> None:
        # livekit's Agent.__init__ is generic over the plugin's own event type, which a strict
        # checker can only read as Unknown; the one ignore is here, at the one call.
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            instructions=instructions, tools=list(tools), llm=llm
        )
        self._writer = writer

    # The three regions in livekit's terms: `instructions` is the static prefix livekit caches and
    # never rebuilds, `chat_ctx` is the history, and the view is appended HERE — after the history,
    # inside the request only — so a view that changes every turn leaves the cached prefix byte for
    # byte the same.
    @override
    async def llm_node(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        chat_ctx: agents.ChatContext,
        tools: list[agents.Tool],
        model_settings: ModelSettings,
    ) -> Node:
        """One request: the view goes last, the deltas become transcripts, the numbers an entry."""
        writer = self._writer
        view = writer.view
        if view:
            chat_ctx.items.append(agents.ChatMessage(role="system", content=[view]))
        await writer.thinking()
        llm = cast(agents.LLM[Any], self.llm)  # pyright: ignore[reportUnknownMemberType]
        with Metered(llm) as metered:
            async for chunk in LiveAgent.default.llm_node(self, chat_ctx, tools, model_settings):
                if isinstance(chunk, agents.ChatChunk):
                    delta = chunk.delta
                    if delta is not None and delta.content:
                        await writer.said(delta.content)
                    yield chunk
                elif isinstance(chunk, str):
                    await writer.said(chunk)
                    yield chunk
            # The stream is drained and closed by now, so livekit has already emitted every
            # metric of this request: metrics.llm goes out before the tools this round asked for.
            await writer.measured(metered.seen)


# livekit owns the history now, so the one moment the platform reaches into it lives here, next
# to the agent it reaches into.
async def remembered(agent: TextAgent, *items: agents.ChatItem) -> None:
    """Items into the history without a request: agent.say is not a turn of the model."""
    context = agent.chat_ctx.copy()
    context.items.extend(items)
    await agent.update_chat_ctx(context, exclude_invalid_function_calls=False)

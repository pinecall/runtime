"""livekit's Agent for a text call: the blocks enter per request, the log is written on the way."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from typing import Any, Protocol, cast, override

from livekit.agents import llm as agents
from livekit.agents.metrics import LLMMetrics as Measured
from livekit.agents.voice import ModelSettings
from livekit.agents.voice.agent import Agent as LiveAgent

from pinecall.providers.blocks import request_context
from pinecall.providers.models import Chat
from pinecall.types import Blocks


class Writer(Protocol):
    """What the agent needs of the session: a hand on the log at the three moments of a request."""

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
    """The agent livekit runs: our prompt's blocks, our tools, and the log written on the way."""

    def __init__(
        self,
        *,
        blocks: Blocks,
        tools: Sequence[agents.Tool],
        llm: Chat,
        writer: Writer,
    ) -> None:
        # livekit's Agent.__init__ is generic over the plugin's own event type, which a strict
        # checker can only read as Unknown; the one ignore is here, at the one call.
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            instructions=blocks.instructions, tools=list(tools), llm=llm
        )
        self._blocks = blocks
        self._writer = writer

    # The prompt in livekit's terms: `instructions` is the static blocks joined, which livekit
    # caches and never rebuilds, `chat_ctx` is the history, and the dynamic blocks are added HERE —
    # after the history, inside the request only — so a view that changes every turn leaves the
    # cached prefix byte for byte the same. The same seam the voice agent cuts at.
    @override
    async def llm_node(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        chat_ctx: agents.ChatContext,
        tools: list[agents.Tool],
        model_settings: ModelSettings,
    ) -> Node:
        """One request: the dynamic blocks last, the deltas as transcripts, the numbers an entry."""
        writer = self._writer
        request = request_context(chat_ctx, self._blocks)
        await writer.thinking()
        llm = cast(agents.LLM[Any], self.llm)  # pyright: ignore[reportUnknownMemberType]
        with Metered(llm) as metered:
            async for chunk in LiveAgent.default.llm_node(self, request, tools, model_settings):
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

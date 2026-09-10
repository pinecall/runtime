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
from pinecall.session.lookups import TurnLookups
from pinecall.types import Blocks
from pinecall_protocol.events import ErrorEvent


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

    @property
    def speech(self) -> str | None:
        """The speech the turn being answered is filed under, or None between two turns."""
        ...

    async def skipped(self, error: ErrorEvent) -> None:
        """A lookup did not run: the entry that says so, and the turn goes on."""
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
        lookups: TurnLookups,
    ) -> None:
        # livekit's Agent.__init__ is generic over the plugin's own event type, which a strict
        # checker can only read as Unknown; the one ignore is here, at the one call.
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            instructions=blocks.instructions, tools=list(tools), llm=llm
        )
        self._blocks = blocks
        self._writer = writer
        self._lookups = lookups

    # livekit's own hook, the one a spoken call runs between the caller's last word and the
    # request (agent_activity.py:2605). A text turn is handed to generate_reply by hand, which
    # never calls it (:3825), so the session calls it here itself, with the very message it hands
    # livekit next — the same seam, the same method, on both channels.
    @override
    async def on_user_turn_completed(
        self,
        turn_ctx: agents.ChatContext,  # noqa: ARG002 — livekit's signature
        new_message: agents.ChatMessage,
    ) -> None:
        """The caller's words are the query: this turn's lookups, or why they did not run."""
        query = new_message.text_content or ""
        for skipped in await self._lookups.turn_ended(query, self._writer.speech):
            await self._writer.skipped(skipped)

    # The prompt in livekit's terms: `instructions` is the static blocks joined, which livekit
    # caches and never rebuilds, `chat_ctx` is the history, and this turn's lookups and the dynamic
    # blocks are added HERE — after the history, inside the request only — so a view that changes
    # every turn leaves the cached prefix byte for byte the same. The seam the voice agent cuts at.
    @override
    async def llm_node(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        chat_ctx: agents.ChatContext,
        tools: list[agents.Tool],
        model_settings: ModelSettings,
    ) -> Node:
        """One request: the view last of all, the deltas as transcripts, the numbers an entry."""
        writer = self._writer
        request = request_context(chat_ctx, self._blocks, self._lookups.items)
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

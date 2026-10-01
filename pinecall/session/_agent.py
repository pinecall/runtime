"""The livekit Agent the session runs: the prompt, the ears' filter, and the hooks around a turn."""

import asyncio
import contextlib
from collections.abc import AsyncIterable, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager
from typing import override

from livekit import rtc
from livekit.agents import (
    llm,
    stt,
)
from livekit.agents.types import TimedString
from livekit.agents.voice import Agent, AgentSession, ModelSettings

from pinecall.session import _prompt, tools
from pinecall.session._livekit import transcript_of
from pinecall.session._prompt import Blocks
from pinecall.wire.events import ErrorEvent

type Heard = AsyncIterator[stt.SpeechEvent]
type Declared = list[llm.Tool | llm.Toolset]
type Thought = AsyncIterator[llm.ChatChunk | str]
type Words = AsyncIterator[str | TimedString]
# What plays while the caller waits: the hold melody after its grace, or nothing.
type Waiting = Callable[[], AbstractAsyncContextManager[None]]


LLM_TIMEOUT = "llm_timeout"


MODEL_SILENT = "the model said nothing within {seconds:g}s, so the caller was asked to say it again"


# How long a turn waits for the model's first word when the agent sets no deadline: past it the
# caller hears MODEL_LATE instead of a silence a model stuck for 20 s would leave them in.
LLM_TIMEOUT_S = 12.0


# What the caller hears when the model is past the deadline, by the agent's language; English
# where the agent's language has no sentence here.
MODEL_LATE = {
    "es": "Disculpe, me demoré. ¿Me lo repite, por favor?",
    "en": "Sorry, that took me too long. Could you say that again?",
    "pt": "Desculpe, demorei. Pode repetir, por favor?",
}


class CallAgent(Agent):
    """livekit's Agent reading the call's prompt blocks, lookups and tools."""

    def __init__(
        self,
        live: AgentSession[None],
        blocks: Blocks,
        lookups: tools.Lookups,
        declared: Declared,
        waiting: Waiting,
    ) -> None:
        """The agent with the static blocks as its instructions and every tool of the call."""
        super().__init__(instructions=blocks.instructions, tools=declared)  # pyright: ignore[reportUnknownMemberType]
        self.live = live
        self.call = lookups.call
        self.blocks = blocks
        self.lookups = lookups
        self.waiting = waiting

    # Runs before the request and livekit times it, so the lookups here are the ones already
    # running (started on an interim) or quick ones. No speech handle exists yet.
    @override
    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """Collect this turn's lookups and write the ones that did not run."""
        for skipped in await self.lookups.turn_ended(new_message.text_content or ""):
            self.call.writing.write("error", skipped)

    # Lookups and dynamic blocks go into this request only, so the cached prefix (instructions
    # and history) stays the same bytes from turn to turn. The wait for the first token plays the
    # melody as a tool's does, and the agent's deadline cuts it, livekit's retries with it.
    @override
    async def llm_node(
        self, chat_ctx: llm.ChatContext, tools: list[llm.Tool], model_settings: ModelSettings
    ) -> Thought:
        """The model run on the history, this turn's lookups, and the dynamic blocks."""
        params = _prompt.request(chat_ctx, self.blocks, self.lookups.items)
        if self.call.context.run is not None:
            self.call.requests.append(_prompt.as_asked(params, tools))
        deadline = self.call.config.llm_timeout_s or LLM_TIMEOUT_S
        thought = Agent.default.llm_node(self, params, tools, model_settings)
        waited = asyncio.timeout(deadline)
        async with contextlib.aclosing(thought):
            try:
                async with self.waiting(), waited:
                    first = await anext(thought, None)
            except TimeoutError:
                # A vendor's own TimeoutError is livekit's to handle, as it was before.
                if not waited.expired():
                    raise
                why = MODEL_SILENT.format(seconds=deadline)
                self.call.writing.write(
                    "error", ErrorEvent(code=LLM_TIMEOUT, message=why, recoverable=True)
                )
                yield _late_in(self.call.config.language)
                return
            if isinstance(first, llm.ChatChunk | str):
                yield first
            async for chunk in thought:
                if isinstance(chunk, llm.ChatChunk | str):
                    yield chunk

    # Runs on what was played, so an interrupted reply's transcript stops where its audio did; a
    # written call plays everything. Aligned speech yields one timed string per word.
    @override
    async def transcription_node(
        self, text: AsyncIterable[str | TimedString], model_settings: ModelSettings
    ) -> Words:
        """Each played piece of the reply, written as it plays, then passed on."""
        async for delta in Agent.default.transcription_node(self, text, model_settings):
            if str(delta):
                self.call.writing.write("agent.transcript", transcript_of(self.live, delta))
            yield delta

    # Before the turn detector: the last point to drop a backchannel at no cost of a model.
    @override
    async def stt_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings
    ) -> Heard:
        """What the ears heard, but agreement said over the agent's voice."""
        async for event in Agent.default.stt_node(self, audio, model_settings):
            if not self._agreement(event):
                yield event

    def _agreement(self, event: stt.SpeechEvent) -> bool:
        text = event.alternatives[0].text if event.alternatives else ""
        return self.call.agent_speaking and self.call.turn_policy.is_a_backchannel(text)


def _late_in(language: str | None) -> str:
    return MODEL_LATE.get((language or "en")[:2].lower(), MODEL_LATE["en"])

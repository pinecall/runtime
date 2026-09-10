"""livekit's Agent for a spoken call: the blocks enter per request, the words leave as they play."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterable, Sequence
from typing import Any, Protocol, override

from livekit import rtc
from livekit.agents import llm as agents
from livekit.agents import stt as recognition
from livekit.agents.types import FlushSentinel, TimedString
from livekit.agents.voice import ModelSettings
from livekit.agents.voice.agent import Agent as LiveAgent

from pinecall.providers.blocks import request_context
from pinecall.session.lookups import TurnLookups
from pinecall.types import Blocks
from pinecall_protocol.events import ErrorEvent


class Speaking(Protocol):
    """What the agent needs of the bridge: where the words go, and whose words they are."""

    def said(self, delta: str | TimedString) -> None:
        """One piece of the reply, as the caller is hearing it, timed when the voice aligned it."""
        ...

    def heard(self, event: recognition.SpeechEvent) -> bool:
        """Whether this is the caller speaking; False drops it before the LLM ever sees it."""
        ...

    async def skipped(self, error: ErrorEvent) -> None:
        """A lookup did not run: the entry that says so, and the turn goes on."""
        ...


type Words = AsyncGenerator[str | TimedString, None]
type Heard = AsyncGenerator[recognition.SpeechEvent, None]
type Thought = AsyncGenerator[agents.ChatChunk | str | FlushSentinel, None]


class VoiceAgent(LiveAgent):
    """The agent livekit runs on a line: our prompt's blocks, our tools, and our ears."""

    def __init__(
        self,
        *,
        blocks: Blocks,
        tools: Sequence[agents.Tool],
        speaking: Speaking,
        lookups: TurnLookups,
    ) -> None:
        # livekit's Agent.__init__ is generic over the plugin's own event type, which a strict
        # checker can only read as Unknown; the one ignore is here, at the one call.
        super().__init__(  # pyright: ignore[reportUnknownMemberType]
            instructions=blocks.instructions, tools=list(tools)
        )
        self._blocks = blocks
        self._speaking = speaking
        self._lookups = lookups

    # livekit's hook between the caller's last word and the request (agent_activity.py:2605), and
    # the one moment the whole turn is known. The lookups usually started long before it, on an
    # interim transcript the bridge handed them (session/voice/events.py), so what happens here is
    # a run being COLLECTED: nothing to wait for when it is back, its tail under
    # PINECALL_VOICE_LOOKUP_BUDGET_MS when it is not, and the whole run when the turn was too short
    # to have started one. The hook is timed by livekit itself, as on_user_turn_completed_delay on
    # the EOU block, which is why that budget is small. No speech exists yet at this moment — the
    # reply's handle is created after the hook returns (:2672) — so the lookup is filed under none.
    @override
    async def on_user_turn_completed(
        self,
        turn_ctx: agents.ChatContext,  # noqa: ARG002 — livekit's signature
        new_message: agents.ChatMessage,
    ) -> None:
        """The caller's turn is over: this turn's lookups collected, or why they did not run."""
        for skipped in await self._lookups.turn_ended(new_message.text_content or "", None):
            await self._speaking.skipped(skipped)

    # The prompt in livekit's terms: `instructions` is the static blocks joined, which livekit
    # caches and never rebuilds, `chat_ctx` is the history, and this turn's lookups and the dynamic
    # blocks are added HERE — after the history, inside the request only — so a view that changes
    # every turn leaves the cached prefix byte for byte the same. The seam the text session cuts at.
    @override
    async def llm_node(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        chat_ctx: agents.ChatContext,
        tools: list[agents.Tool],
        model_settings: ModelSettings,
    ) -> Thought:
        """One request, with the view last of all and the history untouched."""
        request = request_context(chat_ctx, self._blocks, self._lookups.items)
        async for chunk in LiveAgent.default.llm_node(self, request, tools, model_settings):
            yield chunk

    # The words the caller is actually hearing, at the moment the audio carries them: this node
    # runs on the played text, not on the model's stream, which is why an interrupted reply
    # leaves a transcript that stops where the audio stopped. With an aligned transcript the
    # session hands this node one TimedString per word (agent_activity.py:3014-3019), so the
    # delta is passed on whole: str() would throw the timings away right where they arrive.
    @override
    async def transcription_node(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, text: AsyncIterable[str | TimedString], model_settings: ModelSettings
    ) -> Words:
        """Every delta of the reply as it plays, into the log and on to livekit unchanged."""
        async for delta in LiveAgent.default.transcription_node(self, text, model_settings):
            if str(delta):
                self._speaking.said(delta)
            yield delta

    # This node stands before the turn detector, which is the last place a word the caller never
    # said can be dropped without the LLM paying for it. What is judged here is the TEXT — a
    # backchannel over the agent's own voice. The ENERGY of the frame was judged here too, until
    # five measured calls showed it decides nothing: docs/decisions/voice-bridge.md.
    @override
    async def stt_node(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings
    ) -> Heard:
        """Every recognised event the bridge accepts as the caller, in the order they arrived."""
        heard: Any = LiveAgent.default.stt_node(self, audio, model_settings)
        async for event in heard:
            if self._speaking.heard(event):
                yield event

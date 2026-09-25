"""One reply's path: the turn in flight, what it says, and the entries that close it."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from livekit.agents import llm as agents
from livekit.agents.metrics import LLMMetrics as Measured

from pinecall.log.entry import Entry
from pinecall.providers.models import vendor_of
from pinecall.session.errors import COMPONENT_FAILED
from pinecall.session.text.measure import Reply, llm_metrics, turn_metrics
from pinecall_protocol import defs
from pinecall_protocol.events import (
    AgentStateChanged,
    AgentTranscript,
    AgentTurnEnded,
    ErrorEvent,
)

if TYPE_CHECKING:
    from pinecall.session.text.session import TextSession


# This is the agent's Writer: livekit's own path through llm_node calls thinking/said/measured on
# the way, and those three moments belong to the reply in flight, not to the call.
class Turns:
    """Every reply of one call, one at a time: the turn in flight, and what it has said so far."""

    def __init__(self, session: TextSession) -> None:
        self._session = session
        self.reply: Reply | None = None
        self.count = 0
        self.last = ""
        self._state_now: defs.AgentState | None = None

    # ── what the agent hands livekit, and what livekit hands back ───────────────

    async def thinking(self) -> None:
        """livekit is about to send a request: a new round of the reply in flight."""
        await self.agent_state("thinking")
        if self.reply is not None:
            self.reply.round()

    async def said(self, text: str) -> None:
        """One delta of the answer: the caller reads it now, and the turn remembers it."""
        await self.agent_state("speaking")
        reply = self.reply
        if reply is not None:
            reply.delta(text)
            await self._session.emit(
                "agent.transcript",
                AgentTranscript(speech_id=reply.speech_id, text=text, final=False),
            )

    async def measured(self, metrics: Sequence[Measured]) -> None:
        """What livekit measured of one request: the turn's numbers and the entry."""
        reply = self.reply
        if reply is None:
            return
        for one in metrics:
            reply.measured(one)
            await self._session.emit("metrics.llm", llm_metrics(one, reply.speech_id))

    @property
    def speech(self) -> str | None:
        """The speech the reply in flight is filed under, or None between two turns."""
        return None if self.reply is None else self.reply.speech_id

    async def skipped(self, error: ErrorEvent) -> None:
        """A lookup did not run: the entry, recoverable, and the reply goes on without it."""
        await self._session.emit("error", error)

    # ── one reply ───────────────────────────────────────────────────────────────

    # Three ways to ask for one reply, never two at once. `heard` is the caller's own words: the
    # lookups run on them first, then they enter the history as the caller's turn. `said`
    # is the app's text entering the history as the caller's words — agent.reply — and no query.
    # `instructions` is one system message of that turn alone (agent_activity.py:3247), which is
    # what a supervisor's whisper is: an order the caller never said and never sees.
    async def answer(
        self,
        speech: str,
        arrived: float,
        *,
        heard: str | None = None,
        said: str | None = None,
        instructions: str | None = None,
    ) -> None:
        """One reply, run by livekit: its rounds of model and tools, and the entries they make."""
        asked = [one for one in (heard, said, instructions) if one is not None]
        assert len(asked) == 1, "a turn is asked for one way at a time"
        reply = Reply(speech_id=speech, arrived=arrived)
        self.reply = reply
        try:
            await self._one_reply(heard, said, instructions)
        except Exception as failed:
            # The spoken session writes what a component said when it broke (voice/events.py);
            # a written turn wrote nothing and the log showed a turn that simply stopped.
            await self._session.emit(
                "error",
                ErrorEvent(code=COMPONENT_FAILED, message=str(failed), recoverable=False),
            )
            raise
        finally:
            self.reply = None
        await self.ended(reply)

    async def _one_reply(
        self, heard: str | None, said: str | None, instructions: str | None
    ) -> None:
        """livekit's generate_reply, the caller's words through the agent's hook on the way."""
        live = self._session.live
        if heard is not None:
            message = agents.ChatMessage(role="user", content=[heard])
            agent = self._session.text_agent
            await agent.on_user_turn_completed(agent.chat_ctx, message)
            await live.generate_reply(user_input=message)
        elif said is not None:
            await live.generate_reply(user_input=said)
        else:
            await live.generate_reply(instructions=instructions or "")

    async def ended(self, reply: Reply) -> None:
        """The reply is over: turn.agent with what was measured, and the agent goes idle."""
        session = self._session
        self.count += 1
        self.last = reply.text
        await session.emit(
            "turn.agent",
            AgentTurnEnded(
                speech_id=reply.speech_id,
                text=reply.text,
                interrupted=False,
                metrics=turn_metrics(
                    reply,
                    e2e_latency=time.monotonic() - reply.arrived,
                    provider=vendor_of(session.llm),
                    model=session.llm.model,
                ),
            ),
        )
        await self.agent_state("idle")

    async def agent_state(self, state: defs.AgentState) -> Entry | None:
        """The agent's state, said once per change: a repeat is not a fact, it is noise."""
        if state == self._state_now:
            return None
        self._state_now = state
        return await self._session.emit("agent.state", AgentStateChanged(state=state))

"""One reply's path: the turn in flight, what it says, and the entries that close it."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import TYPE_CHECKING

from livekit.agents.metrics import LLMMetrics as Measured

from pinecall.log.entry import Entry
from pinecall.providers.models import vendor_of
from pinecall.session.text.measure import Reply, llm_metrics, turn_metrics
from pinecall_protocol import defs
from pinecall_protocol.events import AgentStateChanged, AgentTranscript, AgentTurnEnded

if TYPE_CHECKING:
    from pinecall.session.text.session import TextSession


# This is the agent's Writer: livekit's own path through llm_node calls thinking/said/measured on
# the way, and those three moments belong to the reply in flight, not to the call. The view is the
# session's — the prompt is call-long — and is read here because a request is a turn.
class Turns:
    """Every reply of one call, one at a time: the turn in flight, and what it has said so far."""

    def __init__(self, session: TextSession) -> None:
        self._session = session
        self.reply: Reply | None = None
        self.count = 0
        self.last = ""
        self._state_now: defs.AgentState | None = None

    # ── what the agent hands livekit, and what livekit hands back ───────────────

    @property
    def view(self) -> str:
        """The dynamic region, read per request so it never enters the cached instructions."""
        return self._session.view

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

    # ── one reply ───────────────────────────────────────────────────────────────

    # The two are not the same request and never both: `said` enters the history as the caller's
    # own words, `instructions` as one system message of that turn alone (agent_activity.py:3247),
    # which is what a supervisor's whisper is — an order the caller never said and never sees.
    async def answer(
        self,
        speech: str,
        arrived: float,
        said: str | None = None,
        instructions: str | None = None,
    ) -> None:
        """One reply, run by livekit: its rounds of model and tools, and the entries they make."""
        assert (said is None) != (instructions is None), "a turn is asked for one way or the other"
        reply = Reply(speech_id=speech, arrived=arrived)
        self.reply = reply
        try:
            live = self._session.live
            handle = (
                live.generate_reply(user_input=said)
                if said is not None
                else live.generate_reply(instructions=instructions or "")
            )
            await handle
        finally:
            self.reply = None
        await self.ended(reply)

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

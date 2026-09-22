"""How a spoken call ends: who decided it, how the session comes down, and what call.ended says."""

from __future__ import annotations

from collections.abc import Callable

from livekit.agents import get_job_context
from livekit.agents.voice import AgentSession
from livekit.agents.voice.events import CloseReason

from pinecall.session.voice.hanging_up import HOW_IT_ENDED
from pinecall.session.voice.writing import Writing
from pinecall_protocol import defs
from pinecall_protocol.events import ToolCall

# Neither the agent nor the caller: something the platform could not answer for.
A_PLATFORM_ERROR: tuple[defs.EndReason, defs.EndedBy] = ("error", "platform")


# One per call. Every way a call can end writes itself down HERE first and lets the session close
# however it closes: livekit's own close reason is a guess at what happened — a caller who left
# because a transfer took them reads as a hang-up, and the model's own end_call reads as a drain —
# and a guess must never win over the fact somebody recorded. See docs/decisions/voice-bridge.md.
class TheEnding:
    """Who ended this call and how, from whichever side decided it, and the shutdown it asks for."""

    def __init__(self, live: Callable[[], AgentSession[None] | None], writing: Writing) -> None:
        self._live = live
        self._writing = writing
        self._ended: tuple[defs.EndReason, defs.EndedBy] | None = None
        self._closed_for: CloseReason | None = None

    # The caller's leg leaves the room as soon as the far end takes a cold transfer, and a session
    # that only saw them go would close as PARTICIPANT_DISCONNECTED — `caller_hung_up`, which is
    # not what happened. Nothing is ended here: the transfer already took the caller away.
    def transferred(self) -> None:
        """A transfer took: the call ends as transferred, whatever closes the session."""
        self._ended = ("transferred", "agent")

    # The same move, for the other thing that ends a call without our asking: livekit's end_call
    # tool closes the session itself, as USER_INITIATED, which HOW_IT_ENDED would read as `drained`
    # by the platform. hanging_up.py holds the why.
    def ended_by_the_model(self) -> None:
        """The model called end_call: this call ended because the agent decided it had."""
        self._ended = ("agent_hung_up", "agent")

    async def a_platform_tool_ran(self, called: ToolCall, result: defs.ToolResult) -> None:
        """The log has every tool the model reached for, livekit's own end_call included."""
        await self._writing.emit("tool.call", called)
        await self._writing.emit("tool.result", result)

    # `by` is the agent unless a supervisor's `end` says so, and that verb alone asks `at_once`.
    async def hangup(
        self, reason: defs.EndReason, by: defs.EndedBy = "agent", *, at_once: bool = False
    ) -> None:
        """call.hangup: the call ends now, and the log will say whose doing it was."""
        self._ended = (reason, by)
        self.shut_down(reason, at_once=at_once)

    # Nobody hung up: a component answered something that will not change — a voice that does not
    # exist, a key that is not accepted — and every second spent retrying it is a caller hearing an
    # apology for silence. The log already has the one error entry that says which.
    def ends_for(self, cause: str) -> None:
        """A component failed for good: the call ends now, as the error nobody could answer."""
        self._ended = A_PLATFORM_ERROR
        self.shut_down(cause)

    def session_closed(self, event: object) -> None:
        """Why livekit closed the session, kept for call.ended."""
        reason = getattr(event, "reason", None)
        if isinstance(reason, CloseReason):
            self._closed_for = reason

    def how_it_ended(self) -> tuple[defs.EndReason, defs.EndedBy]:
        """Who ended the call: whoever wrote it down, else whatever closed the session."""
        if self._ended is not None:
            return self._ended
        if self._closed_for is not None:
            return HOW_IT_ENDED[self._closed_for]
        return A_PLATFORM_ERROR

    # The job is what ends a call: the session alone leaves the room open and the worker waiting.
    def shut_down(self, reason: str, *, at_once: bool = False) -> None:
        """Take the session and the job down together, whoever decided the call was over."""
        live = self._live()
        if live is not None:
            live.shutdown(drain=not at_once)
        job = get_job_context(required=False)
        if job is not None:
            job.shutdown(reason=reason)

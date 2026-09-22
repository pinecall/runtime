"""The six supervise verbs on one live call: the entry first, then what livekit does about it."""

from __future__ import annotations

from livekit.agents.voice import AgentSession
from livekit.agents.voice.agent import Agent

from pinecall.session.supervising import (
    A_RELEASE,
    A_WHISPER,
    ALREADY_HELD,
    BY_A_SUPERVISOR,
    NOBODY_HOLDS,
    THE_SUPERVISOR,
)
from pinecall.session.voice.commands import Ending
from pinecall.session.voice.writing import Writing
from pinecall_protocol import ProtocolError, verbs
from pinecall_protocol.commands import CallTransfer, SupervisorVerb
from pinecall_protocol.defs import Supervisor
from pinecall_protocol.events import (
    SupervisorEnded,
    SupervisorReleased,
    SupervisorSaid,
    SupervisorTookOver,
    SupervisorTransferred,
    SupervisorWhispered,
)


# One per call, built by the bridge the moment it has a session, because `taken_by` has to survive
# between two commands: a takeover and the release that answers it are two frames minutes apart.
# Nothing here is module-level; a worker process runs one call.
class Supervising:
    """The desk's hand on a live call: who is holding the line, and what each verb does."""

    def __init__(
        self, live: AgentSession[None], agent: Agent, writing: Writing, ending: Ending
    ) -> None:
        self._live = live
        self._agent = agent
        self._writing = writing
        self._ending = ending
        self.taken_by: Supervisor | None = None

    # The transfer is the one verb this class does not finish itself: `call.transfer` already has
    # an applier that sends the leg on, writes call.transferred and ends the call when it took, and
    # a second copy of that would be a second answer to "did the transfer work". So the verb is
    # written into the log here and the command handed back for that one applier to run.
    async def apply(self, command: SupervisorVerb) -> CallTransfer | None:
        """One verb: its entry, then the session. A transfer comes back for the transfer applier."""
        by, verb = command.by, command.verb
        match verb:
            case verbs.SayVerb():
                await self._say(by, verb.text)
            case verbs.WhisperVerb():
                await self._whisper(by, verb.text)
            case verbs.TakeoverVerb():
                await self._take_over(by)
            case verbs.ReleaseVerb():
                await self._release(by)
            case verbs.EndVerb():
                await self._end(by, verb.reason)
            case verbs.TransferVerb():
                await self._writing.emit(
                    "supervisor.transferred",
                    SupervisorTransferred(by=by, to=verb.to, mode=verb.mode),
                )
                return CallTransfer(to=verb.to, mode=verb.mode)
        return None

    # ── the six verbs ───────────────────────────────────────────────────────────

    # session.say verbatim (agent_session.py:1430): no model, no tools, and the sentence enters the
    # history as the agent's own, exactly as the app's agent.say does.
    async def _say(self, by: Supervisor, text: str) -> None:
        """say: the agent says the supervisor's words, word for word, to the caller."""
        await self._writing.emit("supervisor.said", SupervisorSaid(by=by, text=text))
        self._live.say(text, allow_interruptions=True)

    async def _whisper(self, by: Supervisor, text: str) -> None:
        """whisper: an instruction the caller never hears, binding from the next sentence on."""
        await self._writing.emit("supervisor.whispered", SupervisorWhispered(by=by, text=text))
        note = A_WHISPER.format(text=text)
        await self._remember(note)
        # A human is on the line: a turn generated now would talk over them.
        if self.taken_by is None:
            self._live.generate_reply(instructions=note)

    async def _take_over(self, by: Supervisor) -> None:
        """takeover: the agent stops mid-sentence and goes mute AND deaf; the human has the line."""
        if self.taken_by is not None:
            raise ProtocolError(ALREADY_HELD.format(id=self.taken_by.id))
        await self._writing.emit("supervisor.took_over", SupervisorTookOver(by=by))
        await self._cut_the_sentence()
        # Deaf as well as mute: what the agent cannot hear, it cannot later claim to remember, and
        # a history with the human's half missing is the one a release must not paper over.
        self._live.output.set_audio_enabled(False)
        self._live.input.set_audio_enabled(False)
        self.taken_by = by

    async def _release(self, by: Supervisor) -> None:
        """release: the agent hears and speaks again, knowing only that it missed something."""
        if self.taken_by is None:
            raise ProtocolError(NOBODY_HOLDS)
        await self._writing.emit("supervisor.released", SupervisorReleased(by=by))
        # Ears before voice: a session that could speak before it could hear would answer into a
        # sentence it never heard the start of.
        self._live.input.set_audio_enabled(True)
        self._live.output.set_audio_enabled(True)
        self.taken_by = None
        await self._remember(A_RELEASE)
        self._live.generate_reply(instructions=A_RELEASE)

    async def _end(self, by: Supervisor, reason: str | None) -> None:
        """end: the desk hangs up now — mid-sentence, mid-thought — and call.ended says who did."""
        await self._writing.emit("supervisor.ended", SupervisorEnded(by=by, reason=reason))
        await self._ending.hangup(BY_A_SUPERVISOR, THE_SUPERVISOR, at_once=True)

    # ── the two livekit calls both a whisper and a release make ─────────────────

    # The note goes at the END of the history (agent.py:236, update_chat_ctx), never into a
    # static block: those are what the provider caches, and a sentence appended there would
    # rebuild the cache for every call this agent ever answers.
    async def _remember(self, note: str) -> None:
        """One system message onto the end of the history, where the model reads it next turn."""
        context = self._agent.chat_ctx.copy()
        context.add_message(role="system", content=note)
        await self._agent.update_chat_ctx(context)

    # interrupt raises when nothing is playing or the session has already stopped
    # (agent_session.py:1534). Neither is a reason to refuse the takeover: the agent being quiet
    # already is the state the verb was asking for.
    async def _cut_the_sentence(self) -> None:
        """The agent's sentence cut where it stands, or nothing when there was none to cut."""
        try:
            await self._live.interrupt(force=True)
        except Exception:  # noqa: BLE001 — a session with nothing to interrupt is not a failure
            return

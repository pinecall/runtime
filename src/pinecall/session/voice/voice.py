"""One spoken call's bridge: the session on one side, the log and the app's tools on the other."""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from livekit.agents import get_job_context
from livekit.agents import stt as recognition
from livekit.agents.types import TimedString
from livekit.agents.voice import AgentSession
from livekit.agents.voice.events import CloseReason, EventTypes, FunctionToolsExecutedEvent

from pinecall._settings import Budgets
from pinecall.log import NOTHING_SAID, hashed_prompt
from pinecall.providers import prices
from pinecall.session.knowing import a_line_for_the_file_it_ships_with
from pinecall.session.lookups import Lookup, NoLookup, TurnLookups
from pinecall.session.remembering import NoRememberer, Rememberer, remembered_within
from pinecall.session.scoring import Scorer, unjudged
from pinecall.session.voice import commands, hearing
from pinecall.session.voice.agent import VoiceAgent
from pinecall.session.voice.barge_in import is_a_backchannel
from pinecall.session.voice.events import Events
from pinecall.session.voice.metrics import Meters
from pinecall.session.voice.platform import Platform
from pinecall.session.voice.room import DataChannel, Facts, Holding
from pinecall.session.voice.supervising import Supervising
from pinecall.session.voice.tools import Tools
from pinecall.session.voice.writing import Writing
from pinecall.types import AgentConfig, Blocks, CallContext
from pinecall_protocol import Command, ProtocolError, defs
from pinecall_protocol.codec import decode_entry
from pinecall_protocol.events import (
    CallEnded,
    CallScore,
    CallStarted,
    CallSummary,
    Custom,
    ErrorEvent,
    PromptChanged,
    StateChanged,
    ToolsChanged,
)
from pinecall_protocol.room import EventReceived

# The session's event that says the tool outputs are about to enter the history, and the one that
# says why the session closed. Named once, beside the subscribe and the unsubscribe, and typed as
# livekit's own Literal, which is the only thing `AgentSession.on` accepts.
TOOLS_EXECUTED: EventTypes = "function_tools_executed"
CLOSED: EventTypes = "close"

# How livekit's own reason for closing the session reads on our wire. JOB_SHUTDOWN is the platform
# taking the worker down with the call still on it (a deploy, a stop, a drain) and USER_INITIATED
# is our own code closing the session outside hangup — the console's Ctrl+C is one — so both are
# `drained`: nobody's fault and not an error. See docs/decisions/voice-bridge.md.
HOW_IT_ENDED: dict[CloseReason, tuple[defs.EndReason, defs.EndedBy]] = {
    CloseReason.PARTICIPANT_DISCONNECTED: ("caller_hung_up", "caller"),
    CloseReason.ERROR: ("error", "platform"),
    CloseReason.JOB_SHUTDOWN: ("drained", "platform"),
    CloseReason.USER_INITIATED: ("drained", "platform"),
    CloseReason.TASK_COMPLETED: ("agent_hung_up", "agent"),
}


# One per call, built by `a_bridge`, and it is the one object livekit's Agent, the command applier
# and the worker's job all talk to: the Bridge the worker holds, the Speaking the agent reads, the
# Prompting and Ending the commands reach. Nothing here is module-level; a process runs one call.
class VoiceBridge:
    """A spoken call as the platform sees it: what was said, measured, asked and answered."""

    def __init__(
        self,
        context: CallContext,
        config: AgentConfig,
        platform: Platform,
        recording: Path | None = None,
        score: Scorer = unjudged,
        lookup: Lookup = NoLookup(),  # noqa: B008 — stateless, shared on purpose
        rememberer: Rememberer = NoRememberer(),  # noqa: B008 — stateless, shared on purpose
        budgets: Budgets = Budgets(),  # noqa: B008 — frozen
    ) -> None:
        self.context = context
        self.config = config
        self.platform = platform
        self.recording = recording
        self._score = score
        self._rememberer = rememberer
        self._budgets = budgets
        self.writing = Writing(platform, context.call)
        self.meters = Meters(self.writing)
        # The platform's own two tools are declared beside the app's, so the model sees one list
        # and the `tools` block describes one list: session/lookups.py. Built before the
        # subscriber, which hands them every interim transcript so a lookup starts while the
        # caller is still talking and the budget only ever covers what is left of it.
        self.lookups = TurnLookups(
            lookup, context.call, context.remembered_as, config, budgets.voice_lookup_ms
        )
        self.events = Events(self.writing, self.meters, self, self.lookups)
        self.tools = Tools(config, platform, context.call, self.writing.emit)
        self.blocks = Blocks(config.prompt, _the_file_it_ships_with(config))
        self._agent = VoiceAgent(
            blocks=self.blocks,
            tools=[*self.tools.declared_tools, *self.lookups.declared_tools],
            speaking=self,
            lookups=self.lookups,
        )
        self._live: AgentSession[None] | None = None
        # Built in opened(), because it needs the session and because who holds the line has
        # to survive between a takeover and the release that answers it.
        self._supervising: Supervising | None = None
        self._holding: Holding | None = None
        self._facts: Facts | None = None
        self._datachannel: DataChannel | None = None
        self._started_at = time.time()
        self._ended: tuple[defs.EndReason, defs.EndedBy] | None = None
        self._closed_for: CloseReason | None = None

    # ── the Bridge the worker holds ─────────────────────────────────────────────

    @property
    def agent(self) -> VoiceAgent:
        """The livekit Agent of this call: our prompt's blocks, our tools, our ears."""
        return self._agent

    async def opened(self, live: AgentSession[None]) -> None:
        """The session is built and about to start: subscribe to it, and write call.started."""
        self._live = live
        self._supervising = Supervising(live, self._agent, self.writing, self)
        self.writing.open()
        self.events.watch(live)
        self.meters.watch(live)
        self._hold_the_room()
        live.on(TOOLS_EXECUTED, self._tools_executed)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        live.on(CLOSED, self._session_closed)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        # `from` is a keyword, so this one event is built from the wire's own key names.
        started = {
            "channel": self.context.channel,
            "direction": self.context.direction,
            "from": self.context.caller,
            "to": self.context.route.number or self.config.slug,
            "caller": None,
            "started_at": self._started_at,
        }
        await self.writing.emit("call.started", CallStarted.model_validate(started))
        await a_line_for_the_file_it_ships_with(self.blocks, self.writing.emit)

    # The shutdown callbacks of a job run gathered, not in order, so the session is closed here
    # first: its own close drains the last speech and adds the last turn to the history, and
    # call.ended must come after that turn and never before it.
    async def closed(self, reason: str) -> None:  # noqa: ARG002 — livekit's string; ours is typed
        """The job is shutting down: call.ended, call.summary with the cost, then the verdict."""
        live = self._live
        if live is not None:
            await live.aclose()
            live.off(TOOLS_EXECUTED, self._tools_executed)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
            live.off(CLOSED, self._session_closed)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        self.events.stop()
        self.meters.stop()
        for watching in (self._facts, self._datachannel):
            if watching is not None:
                watching.stop()
        ended, by = self._how_it_ended()
        ended_at = time.time()
        duration = ended_at - self._started_at
        await self.writing.emit(
            "call.ended",
            CallEnded(reason=ended, ended_by=by, ended_at=ended_at, duration_s=duration),
        )
        await self._remember()
        rows = self.meters.rows
        await self.writing.emit(
            "call.summary",
            CallSummary(
                reason=ended,
                outcome=self.events.last_said or NOTHING_SAID,
                duration_s=duration,
                turns=self.events.turns,
                usage=rows,
                cost=prices.cost_of(rows),
                recording=str(self.recording) if self.recording is not None else None,
            ),
        )
        await self.writing.emit("call.score", await self._the_verdict_on_it())
        await self.writing.close()

    # After call.ended and before call.summary, flushed first: the platform reads the turns back
    # off the log this process writes to, and the last one has to be there. An agent that declared
    # no memory has nothing to remember and asks nobody. See docs/decisions/memory.md.
    async def _remember(self) -> None:
        """What this call taught about the contact, written by the platform; a miss is an entry."""
        if self.config.memory is None:
            return
        await self.writing.flushed()
        failed = await remembered_within(
            self._rememberer, self.context.call, self._budgets.remember_s
        )
        if failed is not None:
            await self.writing.emit("error", failed)

    # A verdict is read by the seqs it names, and this process never learns one: the platform
    # numbers the log. So the call is read back through the same door it was written to, which is
    # the whole of what a spoken call may know about the platform. See docs/decisions/scoring.md.
    async def _the_verdict_on_it(self) -> CallScore:
        """This call's own log back from the platform, and what the judge makes of it."""
        await self.writing.flushed()
        entries = [decode_entry(raw) async for raw in self.platform.since(self.context.call, 0)]
        return await self._score(entries, self.config)

    # ── the Speaking the agent reads ────────────────────────────────────────────

    def said(self, delta: str | TimedString) -> None:
        """One piece of the reply, as the caller is hearing it, timed when the voice aligned it."""
        self.events.said(delta)

    # The one thing judged here is the text, and only what is heard over the agent's own voice. A
    # "sí, sí" while the agent explains is the caller listening, and livekit's interruption reads
    # the running transcript (agent_activity.py:2146), so a backchannel that never reaches it never
    # cuts the agent off — and never becomes a turn after the agent stops.
    def heard(self, event: recognition.SpeechEvent) -> bool:
        """Whether this is the caller speaking; False drops it before the LLM ever sees it."""
        text = event.alternatives[0].text if event.alternatives else ""
        return not (text and self._agent_is_speaking() and is_a_backchannel(text))

    async def skipped(self, error: ErrorEvent) -> None:
        """A lookup did not run: the entry, recoverable, and the reply goes on without it."""
        await self.writing.emit("error", error)

    # ── the app's commands ──────────────────────────────────────────────────────

    async def apply(self, command: Command) -> None:
        """One protocol command onto this call: the session, the prompt, or the ending."""
        if self._live is None:
            raise ProtocolError(f"{command.type}: the call has no session yet")
        applying = commands.Applying(self._live, self, self, self, self._holding, self._supervising)
        await commands.apply(applying, command)

    # The static blocks are livekit's instructions, rewritten only when their joined text moved:
    # the same bytes again would still cost the provider a cache write. A dynamic block is read
    # per request, in llm_node, and touches nothing here.
    async def set_prompt(self, name: str, text: str) -> None:
        """prompt.set: one block rewritten. The text stays out of the log; its hash goes in."""
        if self.blocks.set(name, text):
            await self._agent.update_instructions(self.blocks.instructions)
        await self.writing.emit(
            "prompt.changed",
            PromptChanged(name=name, hash=hashed_prompt(text), chars=len(text)),
        )

    # Never livekit's update_tools: the agent keeps every declared tool for the life of the call,
    # and the gate in our callable holds the closed ones shut. See session/visibility.py.
    async def set_tools(self, tools: Sequence[defs.ToolSpec]) -> None:
        """tools.set: the subset of the declared tools the model may call in this state."""
        visible = self.tools.visibility.narrow(tools)
        await self.writing.emit("tools.changed", ToolsChanged(visible=list(visible)))

    # ── what the app writes into the log through this call ──────────────────────

    async def set_state(self, state: Mapping[str, Any], changed: Sequence[str]) -> None:
        """state.set: the app's state moved, and the whole of it goes into this call's log."""
        await self.writing.emit(
            "state.changed", StateChanged(state=dict(state), changed=list(changed))
        )
        self._tell_the_ears(state)

    # The class already knows who it is talking to, so the ears are told: the patient's name the
    # moment a tool identified them, the doctor the moment one is chosen. That is the half a
    # declared list cannot have — a clinic cannot list every patient. livekit replaces the
    # session's own keyterms in place and leaves any it detected alone (agent_session.py:1366).
    def _tell_the_ears(self, state: Mapping[str, Any]) -> None:
        """The words the ears should expect now that the state has moved, on ears that take them."""
        live = self._live
        if live is None or not hearing.takes_keyterms(live.stt):  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
            return
        live.update_options(keyterms=hearing.words(self.config, state))

    async def receives(self, name: str, data: Mapping[str, Any]) -> None:
        """call.event: a declared fact from the tenant's backend; an undeclared one is refused."""
        if not self.config.accepts(name, "app"):
            raise ProtocolError(
                f"agent {self.config.slug} never declared the event {name!r} from the app: "
                f"declare it in agent.configure before sending it"
            )
        await self.writing.emit(
            "event.received", EventReceived(name=name, data=dict(data), source="app")
        )

    async def log_custom(self, name: str, data: Mapping[str, Any]) -> None:
        """call.log: a line of the app's own, with a seq like everything else."""
        await self.writing.emit("custom", Custom(name=name, data=dict(data)))

    # The caller's leg leaves the room as soon as the far end takes the call, and a session that
    # only saw them go would close as PARTICIPANT_DISCONNECTED — `caller_hung_up`, which is not what
    # happened. Nothing is ended here: the transfer already took the caller away.
    def transferred(self) -> None:
        """A cold transfer took: the call ends as transferred, whatever closes the session."""
        self._ended = ("transferred", "agent")

    # `by` is the agent unless somebody says otherwise: a supervisor's `end` verb is the one
    # caller of this that did not come from the agent's own turn, and call.ended must say so.
    async def hangup(self, reason: defs.EndReason, by: defs.EndedBy = "agent") -> None:
        """call.hangup: the call ends now, and the log will say whose doing it was."""
        self._ended = (reason, by)
        self._shut_down(reason)

    # ── the room ────────────────────────────────────────────────────────────────

    # The job is what holds the room and the server API (job.py:438), and livekit hands the job to
    # whoever runs inside it. The room is watched before the job connects, so the first fact it
    # yields is room.opened. A call with no job — a test, a text session — has no room, and the
    # room verbs say so by name.
    def _hold_the_room(self) -> None:
        """The room's facts into the log, and the DataChannel to the widgets, for this one call."""
        job = get_job_context(required=False)
        if job is None:
            return
        self._holding = Holding(
            room=job.room, api=job.api, writing=self.writing, channel=self.context.channel
        )
        self._facts = Facts(self.writing, self.context.channel, self.context.caller)
        self._facts.watch(job.room)
        self._datachannel = DataChannel(
            self._holding, self.platform, self.config, self.context.call
        )
        self._datachannel.watch(job.room)

    # ── what the session tells us on the way ────────────────────────────────────

    # Nobody hung up: a component answered something that will not change — a voice that does not
    # exist, a key that is not accepted — and every second spent retrying it is a caller hearing an
    # apology for silence. The log already has the one error entry that says which. See
    # docs/decisions/providers.md.
    def ends_for(self, cause: str) -> None:
        """A component failed for good: the call ends now, as the error nobody could answer."""
        self._ended = ("error", "platform")
        self._shut_down(cause)

    # The outputs are about to enter the history (agent_activity.py:3721), so a read-back said now
    # lands behind them and before the caller's next words. Only a tool that did what the sentence
    # says is read back: a failure is the model's to explain in its own turn.
    def _tools_executed(self, event: FunctionToolsExecutedEvent) -> None:
        """The read-back of every confirm-declared tool that just ran, spoken as the agent's own."""
        for output in event.function_call_outputs:
            read_back = self.tools.read_backs.pop(output.call_id, None)
            if read_back and not output.is_error and self._live is not None:
                self._live.say(read_back)

    def _session_closed(self, event: object) -> None:
        """Why livekit closed the session, kept for call.ended."""
        reason = getattr(event, "reason", None)
        if isinstance(reason, CloseReason):
            self._closed_for = reason

    def _how_it_ended(self) -> tuple[defs.EndReason, defs.EndedBy]:
        """Who ended the call: the agent when it hung up, else whatever closed the session."""
        if self._ended is not None:
            return self._ended
        if self._closed_for is not None:
            return HOW_IT_ENDED[self._closed_for]
        return ("error", "platform")

    # The job is what ends a call, and livekit hands the job to whoever runs inside it. Ending the
    # session alone would leave the room open and the worker waiting on nobody.
    def _shut_down(self, reason: str) -> None:
        """Take the session and the job down together, whoever decided the call was over."""
        if self._live is not None:
            self._live.shutdown()
        job = get_job_context(required=False)
        if job is not None:
            job.shutdown(reason=reason)

    def _agent_is_speaking(self) -> bool:
        """Whether the caller's words are landing on top of the agent's own audio."""
        return self._live is not None and self._live.agent_state == "speaking"


def a_bridge(
    context: CallContext,
    config: AgentConfig,
    platform: Platform,
    recording: Path | None = None,
    score: Scorer = unjudged,
    lookup: Lookup = NoLookup(),  # noqa: B008 — stateless, shared on purpose
    rememberer: Rememberer = NoRememberer(),  # noqa: B008 — stateless, shared on purpose
    budgets: Budgets = Budgets(),  # noqa: B008 — frozen
) -> VoiceBridge:
    """The Bridging the worker is built with: one call in, its bridge out."""
    return VoiceBridge(context, config, platform, recording, score, lookup, rememberer, budgets)


# The class's own file, as the declaration carried it. A class that ships none has an empty
# knowledge block, which sends nothing at all.
def _the_file_it_ships_with(config: AgentConfig) -> str:
    """The text of the file this agent knows by heart, or nothing."""
    return config.knowledge.text if config.knowledge is not None else ""

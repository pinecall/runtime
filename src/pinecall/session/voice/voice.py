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
from livekit.agents.voice.events import EventTypes, FunctionToolsExecutedEvent

from pinecall._settings import Budgets
from pinecall.log import NOTHING_SAID, hashed_prompt
from pinecall.providers import prices
from pinecall.session.first_entries import started
from pinecall.session.knowing import a_line_for_the_file_it_ships_with
from pinecall.session.lookups import Lookup, NoLookup, TurnLookups
from pinecall.session.remembering import NoRememberer, Rememberer, remembered_within
from pinecall.session.scoring import Scorer, unjudged
from pinecall.session.voice import closing_time, commands, hearing
from pinecall.session.voice.agent import VoiceAgent
from pinecall.session.voice.attending import Attending
from pinecall.session.voice.barge_in import is_a_backchannel
from pinecall.session.voice.ending import TheEnding
from pinecall.session.voice.events import Events
from pinecall.session.voice.hanging_up import a_way_to_hang_up
from pinecall.session.voice.hold import HoldMusic
from pinecall.session.voice.line import Line
from pinecall.session.voice.metrics import Meters
from pinecall.session.voice.platform import Dialled, Platform, PlatformRefused
from pinecall.session.voice.recording import Recorder
from pinecall.session.voice.room import DataChannel, Facts, Holding, Trunks
from pinecall.session.voice.supervising import Supervising
from pinecall.session.voice.tools import Tools
from pinecall.session.voice.writing import Writing
from pinecall.types import AgentConfig, Blocks, CallContext
from pinecall_protocol import Command, ProtocolError, defs
from pinecall_protocol.codec import decode_entry
from pinecall_protocol.events import (
    CallEnded,
    CallScore,
    CallSummary,
    ErrorEvent,
    PromptChanged,
    ToolsChanged,
)

# The session's event that says the tool outputs are about to enter the history, and the one that
# says why the session closed. Named once, beside the subscribe and the unsubscribe, and typed as
# livekit's own Literal, which is the only thing `AgentSession.on` accepts.
TOOLS_EXECUTED: EventTypes = "function_tools_executed"
CLOSED: EventTypes = "close"


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
        # Every way this call can end, and the one place that remembers which it was.
        self.ending = TheEnding(lambda: self._live, self.writing)
        self.meters = Meters(self.writing)
        # The platform's own two tools are declared beside the app's, so the model sees one list
        # and the `tools` block describes one list: session/lookups.py. Built before the
        # subscriber, which hands them every interim transcript so a lookup starts while the
        # caller is still talking and the budget only ever covers what is left of it.
        self.lookups = TurnLookups(
            lookup, context.call, context.remembered_as, config, budgets.voice_lookup_ms
        )
        self.events = Events(self.writing, self.meters, self.ending, self.lookups)
        self.tools = Tools(config, platform, context.call, self.writing.emit)
        self.recorder = Recorder(config, context, self.writing, self._tell_the_ears)
        self.blocks = Blocks(config.prompt, _the_file_it_ships_with(config))
        self._agent = VoiceAgent(
            blocks=self.blocks,
            tools=[
                *self.tools.declared_tools,
                *self.lookups.declared_tools,
                *a_way_to_hang_up(config, self.ending),
            ],
            speaking=self,
            lookups=self.lookups,
        )
        self._live: AgentSession[None] | None = None
        # Built in opened(), because it needs the session and because who holds the line has
        # to survive between a takeover and the release that answers it.
        self._supervising: Supervising | None = None
        self._line: Line | None = None
        self._attending: Attending | None = None
        self._holding: Holding | None = None
        self._facts: Facts | None = None
        self._datachannel: DataChannel | None = None
        self._started_at = time.time()

    # ── the Bridge the worker holds ─────────────────────────────────────────────

    @property
    def agent(self) -> VoiceAgent:
        """The livekit Agent of this call: our prompt's blocks, our tools, our ears."""
        return self._agent

    async def opened(self, live: AgentSession[None]) -> None:
        """The session is built and about to start: subscribe to it, and write call.started."""
        self._live = live
        self.writing.open()
        self.events.watch(live)
        self.meters.watch(live)
        self._hold_the_room()
        # The line and the ask: what call.hold, call.attention and a supervisor taking over all
        # act on. The melody is asked for when the line is held, because its track is published
        # later, once the room is live.
        self._line = Line(live, self.writing, lambda: self.tools.hold)
        self._attending = Attending(self._line, self.writing)
        # After the room, because a transfer asked for at the desk is cold or warm by what is in
        # it: a caller on a SIP leg is sent on, a caller in a browser has somebody dialled in.
        self._supervising = Supervising(
            live, self._agent, self.writing, self.ending, self._holding, self._attending
        )
        # A tool that comes back while a supervisor is holding the line leaves the model nothing
        # to say: they are speaking, and a reply generated over them would enter the history as
        # words the caller never heard.
        self.tools.a_person_has_the_line = self._taken

        live.on(TOOLS_EXECUTED, self._tools_executed)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        live.on(CLOSED, self.ending.session_closed)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        await self.writing.emit(
            "call.started",
            started(self.context, self.context.route.number or self.config.slug, self._started_at),
        )
        await a_line_for_the_file_it_ships_with(self.blocks, self.writing.emit)

    async def closing_time(self) -> None:
        """The agent's limit on this voice call, kept: warned a minute before, ended at it."""
        if self._live is not None:
            await closing_time.keep(
                self.config.max_duration_s, self._live, self.ending, self._taken
            )

    async def holding(self, melody: Path | None) -> None:
        """The room is live: what the caller hears while a tool runs, or None for nothing."""
        self.tools.hold = await HoldMusic.in_this_room(melody, self._has_the_floor)

    # The shutdown callbacks of a job run gathered, not in order, so the session is closed here
    # first: its own close drains the last speech and adds the last turn to the history, and
    # call.ended must come after that turn and never before it.
    async def closed(self, reason: str) -> None:  # noqa: ARG002 — livekit's string; ours is typed
        """The job is shutting down: call.ended, call.summary with the cost, then the verdict."""
        live = self._live
        if live is not None:
            await live.aclose()
            live.off(TOOLS_EXECUTED, self._tools_executed)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
            live.off(CLOSED, self.ending.session_closed)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
        self.events.stop()
        self.meters.stop()
        if self._attending is not None:
            self._attending.close()
        for watching in (self._facts, self._datachannel):
            if watching is not None:
                watching.stop()
        ended, by = self.ending.how_it_ended()
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
        return not (text and self._has_the_floor() and is_a_backchannel(text))

    async def skipped(self, error: ErrorEvent) -> None:
        """A lookup did not run: the entry, recoverable, and the reply goes on without it."""
        await self.writing.emit("error", error)

    # ── the app's commands ──────────────────────────────────────────────────────

    async def apply(self, command: Command) -> None:
        """One protocol command onto this call: the session, the prompt, or the ending."""
        if self._live is None:
            raise ProtocolError(f"{command.type}: the call has no session yet")
        applying = commands.Applying(
            self._live,
            self,
            self.ending,
            self.recorder,
            self._holding,
            self._supervising,
            self._line,
            self._attending,
        )
        await commands.apply(applying, command)

    # The static blocks are livekit's instructions, rewritten only when their joined text moved:
    # the same bytes would still cost a cache write. A dynamic block is read in llm_node.
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

    # Asked whenever a verb dials a second leg — a warm transfer, an invite — and never on a call
    # that dials none, which is almost all of them. The gateway answers with the org's trunk only
    # once the number has passed the org's guards, so a refusal carries the guard's own sentence
    # into the call's log instead of the verb guessing at "no trunk".
    async def _outbound_trunk(self, to: str) -> Dialled:
        """What this call may dial `to` with, or why the platform said it may not."""
        route = self.context.route
        try:
            return await self.platform.outbound_trunk(
                route.agent,
                org=route.org,
                env=route.env,
                holder=self.context.holder,
                to=to,
                call=self.context.call,
            )
        except PlatformRefused as refused:
            return Dialled(refused=str(refused))

    # ── what the app writes into the log through this call ──────────────────────

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
            room=job.room,
            api=job.api,
            writing=self.writing,
            channel=self.context.channel,
            trunks=Trunks(self._outbound_trunk),
        )
        self._facts = Facts(self.writing, self.context.channel, self.context.caller)
        self._facts.watch(job.room)
        self._datachannel = DataChannel(
            self._holding, self.platform, self.config, self.context.call
        )
        self._datachannel.watch(job.room)

    # ── what the session tells us on the way ────────────────────────────────────

    # Nothing to speak here any more: a read-back is said inside the tool that earned it
    # (tools.py), which is where livekit documents speaking around a tool and the only point at
    # which the model has not yet written its account of the result. Kept as the place that
    # forgets a read-back the model never got to hear, so a failed tool leaves nothing behind.
    def _tools_executed(self, event: FunctionToolsExecutedEvent) -> None:
        """Whatever a tool left unsaid — it failed, or the turn died — is dropped here."""
        for output in event.function_call_outputs:
            self.tools.read_backs.pop(output.call_id, None)

    def _taken(self) -> bool:
        """Whether a person at the desk is holding this line right now."""
        return self._supervising is not None and self._supervising.taken_by is not None

    def _has_the_floor(self) -> bool:
        """Whether the agent is speaking: a caller landing on it, a tool that must wait for it."""
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
    return config.knowledge or ""

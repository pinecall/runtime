"""One text call: a livekit AgentSession runs the model, and this writes the log from its path."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from livekit.agents import llm as agents
from livekit.agents.voice import AgentSession

from pinecall._settings import Budgets
from pinecall.log import NOTHING_SAID, hashed_prompt
from pinecall.log.entry import Entry
from pinecall.log.logs import CallLog
from pinecall.providers import prices
from pinecall.providers.models import Chat
from pinecall.session import clock, greeting
from pinecall.session.asking import Asking, NotAsking
from pinecall.session.declaring import declared
from pinecall.session.first_entries import started
from pinecall.session.knowing import a_line_for_the_file_it_ships_with
from pinecall.session.lookups import Lookup, NoLookup, TurnLookups
from pinecall.session.remembering import NoRememberer, Rememberer, remembered_within
from pinecall.session.scoring import Scorer, unjudged
from pinecall.session.text.agent import TextAgent, remembered
from pinecall.session.text.measure import Reply, usage_rows
from pinecall.session.text.running import Running
from pinecall.session.text.turns import Turns
from pinecall.types import AgentConfig, Blocks, CallContext
from pinecall_protocol import WireModel, defs, encode
from pinecall_protocol.commands import StateSet
from pinecall_protocol.defs import EndedBy, Supervisor
from pinecall_protocol.events import (
    CallEnded,
    CallScore,
    CallSummary,
    Custom,
    PromptChanged,
    StateCause,
    StateCauseEvent,
    StateChanged,
    ToolsChanged,
    UserTurnEnded,
)
from pinecall_protocol.metrics import UserTurnMetrics
from pinecall_protocol.room import EventReceived

# livekit bounds its own model→tools→model loop with this and, on the last step, forces a final
# answer with tool_choice="none": a model that will not converge still leaves the caller a sentence.
MAX_TOOL_STEPS = 8

# What a reader of this call is: the caller's chat socket, and the app's own socket.
type Watcher = Callable[[Entry], Awaitable[None]]


class TextSession:
    """One conversation in text: no audio, no room, and the same log a phone call writes."""

    def __init__(
        self,
        context: CallContext,
        config: AgentConfig,
        log: CallLog,
        llm: Chat,
        score: Scorer = unjudged,
        lookup: Lookup = NoLookup(),  # noqa: B008 — stateless, shared on purpose
        rememberer: Rememberer = NoRememberer(),  # noqa: B008 — stateless, shared on purpose
        budgets: Budgets = Budgets(),  # noqa: B008 — frozen
        asking: Asking = NotAsking(),  # noqa: B008 — stateless, shared on purpose
    ) -> None:
        self.context = context
        self.config = config
        self.llm = llm
        self._score = score
        self._rememberer = rememberer
        self._budgets = budgets
        # Where the session was when the app's next state.set arrives: running.py stamps a tool
        # here, receives() stamps an event, and set_state takes it and clears it.
        self.cause: StateCause | None = None
        # Who at the desk is holding this thread, when somebody is: it has to survive between two
        # commands, because a takeover and the release that answers it are minutes apart.
        self.taken_by: Supervisor | None = None
        self._log = log
        self._watchers: list[Watcher] = []
        self._blocks = Blocks(config.prompt, _the_file_it_ships_with(config))
        self._state: dict[str, Any] = {}
        self._speeches = 0
        self._started_at = time.time()
        self._ended = False
        self.turns = Turns(self)
        self.running = Running(self, config)
        # The platform's own two tools are declared beside the app's, so the model sees one list
        # and the `tools` block describes one list: session/lookups.py. A written caller sends a
        # whole message and there is no interim to start a lookup on, so the whole of it runs when
        # the turn ends — under the text budget, because nobody is listening to a chat's silence.
        self.lookups = TurnLookups(
            lookup, context.call, context.remembered_as, config, budgets.text_lookup_ms
        )
        # Every declared tool, once, for the life of the call: livekit only runs a tool it holds,
        # so the declaration IS the registration, and a tools.set narrows `visibility` instead.
        self.text_agent = TextAgent(
            blocks=self._blocks,
            tools=[*declared(config.tools, self.running.ran), *self.lookups.declared_tools],
            llm=llm,
            writer=self.turns,
            lookups=self.lookups,
            # Nobody by default: only a run that has to be reproduced keeps the requests, and it
            # is the run that hands the holder in. session/asking.py.
            asking=asking,
        )
        # vad=None keeps livekit from building a silero client a text call would never listen to,
        # and "manual" turn detection is the truth of a text call: every turn is a frame the caller
        # sent, handed to generate_reply by hand, so livekit builds no detector and warns of no VAD.
        self.live: AgentSession[None] = AgentSession(
            llm=llm,
            vad=None,
            turn_handling={"turn_detection": "manual"},
            max_tool_steps=MAX_TOOL_STEPS,
        )

    @property
    def call(self) -> str:
        """The call this session runs, as every entry of it is filed."""
        return self.context.call

    @property
    def agent(self) -> str:
        """The agent answering, as the registry knows it."""
        return self.config.slug

    def watch(self, watcher: Watcher) -> None:
        """Send every entry of this call there too, unprojected: the sink applies projections."""
        self._watchers.append(watcher)

    # ── the call ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Media is up, which in text means the socket is open and the first turn may arrive."""
        # No room and no job: livekit builds no RoomIO and publishes nothing, which is the
        # headless mode a text call runs in.
        await self.live.start(  # pyright: ignore[reportUnknownMemberType]
            self.text_agent, record=False
        )
        # The pair a voice call opens with too (worker/entry.py): seeded once, here, before the app
        # has rendered a thing, so a caller who writes "mañana" is read by a model with a calendar.
        await remembered(self.text_agent, *clock.dated(self.context.today))
        await self.emit("call.started", started(self.context, self.agent, self._started_at))
        await a_line_for_the_file_it_ships_with(self._blocks, self.emit)
        # After call.started, so the opening is a turn INSIDE the call and not before it. A
        # written turn cannot be cut short, so the flag a spoken greeting carries is dropped here
        # rather than pretended at: nobody is talking over anybody in a chat.
        await greeting.open_the_call(
            greeting.the_greeting_for(self.config.greeting, self.context.run),
            say=lambda text, _interruptible: self.say(text),
            reply=lambda instructions, _interruptible: self.reply(instructions),
        )

    async def hangup(self, reason: defs.EndReason, by: EndedBy) -> None:
        """The last three entries of the call, then the log is sealed. Twice is once."""
        if self._ended:
            return
        self._ended = True
        # Closed first, while the activity can still schedule its own on_exit: closing it after
        # the log is sealed abandons that coroutine. Nothing below needs the model any more.
        await self.live.aclose()
        ended_at = time.time()
        duration = ended_at - self._started_at
        await self.emit(
            "call.ended",
            CallEnded(
                reason=reason,
                ended_by=by,
                ended_at=ended_at,
                duration_s=duration,
            ),
        )
        await self._remember()
        usage = usage_rows(self.live.usage)
        await self.emit(
            "call.summary",
            CallSummary(
                reason=reason,
                outcome=self.turns.last or NOTHING_SAID,
                duration_s=duration,
                turns=self.turns.count,
                usage=list(usage),
                cost=prices.cost_of(usage),
            ),
        )
        await self.emit("call.score", await self._the_verdict_on_it())

    # After call.ended and before call.summary: every turn is in the log, which is what memory
    # reads back, and the seal still comes whatever memory did. An agent that declared no memory
    # has nothing to remember and asks nobody. See docs/decisions/memory.md.
    async def _remember(self) -> None:
        """What this call taught about the contact, written by the platform; a miss is an entry."""
        if self.config.memory is None:
            return
        failed = await remembered_within(self._rememberer, self.call, self._budgets.remember_s)
        if failed is not None:
            await self.emit("error", failed)

    # The judge reads the call's own log rather than the session's history, because a verdict is
    # read by the seqs it names and a ChatContext carries none. This session holds the log itself,
    # so it reads it back whole. See docs/decisions/scoring.md.
    async def _the_verdict_on_it(self) -> CallScore:
        """What the judge makes of this call, as the entry the log seals on."""
        return await self._score(await self._log.whole(), self.config)

    # ── what the caller says ────────────────────────────────────────────────────

    async def hears(self, text: str) -> None:
        """The caller wrote something: their turn is logged, then the model answers it."""
        arrived = time.monotonic()
        speech = self._a_speech_id()
        self.turns.count += 1
        await self.emit(
            "turn.user",
            UserTurnEnded(speech_id=speech, text=text, metrics=UserTurnMetrics()),
        )
        # A human holds the thread: the words are in the log, and the model neither answers them
        # nor keeps them — it did not hear them, the same rule a spoken takeover goes deaf under.
        # See docs/decisions/supervise.md.
        if self.taken_by is not None:
            return
        await self.turns.answer(speech, arrived, heard=text)

    # The instruction enters the model's history as a user message and never as a turn.user: the
    # log would otherwise claim the caller spoke words nobody spoke.
    async def reply(self, instructions: str) -> None:
        """agent.reply: one turn now, guided by an instruction the caller never sees or hears."""
        await self.turns.answer(self._a_speech_id(), time.monotonic(), said=instructions)

    # The other half of a whisper: the note is already the last system message of the history, and
    # this makes the model obey it on the very next sentence instead of the one after.
    async def nudged(self, instructions: str) -> None:
        """One turn now, guided as INSTRUCTIONS: nothing enters the history as anybody's words."""
        await self.turns.answer(self._a_speech_id(), time.monotonic(), instructions=instructions)

    async def say(self, text: str) -> None:
        """agent.say: the agent says this, verbatim, with no model in the loop at all."""
        reply = Reply(speech_id=self._a_speech_id(), arrived=time.monotonic(), said=[text])
        await remembered(self.text_agent, agents.ChatMessage(role="assistant", content=[text]))
        await self.turns.ended(reply)

    # ── what the outside world says ─────────────────────────────────────────────

    # An event the agent never declared is refused by name upstream and leaves no trace here.
    async def receives(self, name: str, data: Mapping[str, Any]) -> Entry:
        """call.event from the app: declared events land as event.received and reach the app."""
        entry = await self.emit(
            "event.received",
            EventReceived(name=name, data=dict(data), source="app"),
        )
        self.cause = StateCauseEvent(kind="event", name=name, seq=entry.seq)
        return entry

    # The static blocks are livekit's instructions, rewritten only when their joined text moved:
    # the same bytes again would still cost the provider a cache write. A dynamic block is read
    # per request, in llm_node, and touches nothing here.
    async def set_prompt(self, name: str, text: str) -> Entry:
        """prompt.set: one block rewritten. The text stays out of the log; its hash goes in."""
        if self._blocks.set(name, text):
            await self.text_agent.update_instructions(self._blocks.instructions)
        return await self.emit(
            "prompt.changed",
            PromptChanged(name=name, hash=hashed_prompt(text), chars=len(text)),
        )

    # Never livekit's update_tools: a re-declared tool throws the provider's whole cache away,
    # and the gate in our callable holds the closed ones shut. See session/visibility.py.
    async def set_tools(self, tools: Sequence[defs.ToolSpec]) -> Entry:
        """tools.set: the subset of the declared tools the model may call in this state."""
        visible = self.running.visibility.narrow(tools)
        return await self.emit("tools.changed", ToolsChanged(visible=list(visible)))

    # The cause is where the session was when the state.set arrived: inside a tool call it is that
    # tool, right after an outside fact it is that fact, on its own it has none and says so.
    async def set_state(self, asked: StateSet) -> Entry:
        """state.set: the app's state moved, and the whole of it travels with what changed it."""
        changed = asked.changed if asked.changed is not None else sorted(asked.state)
        self._state = dict(asked.state)
        said: dict[str, Any] = {"state": dict(self._state), "changed": list(changed)}
        if self.cause is not None:
            said["cause"] = self.cause
            self.cause = None
        return await self.emit("state.changed", StateChanged(**said))

    async def log_custom(self, name: str, data: Mapping[str, Any]) -> Entry:
        """call.log: a line of the app's own, with a seq like everything else."""
        return await self.emit("custom", Custom(name=name, data=dict(data)))

    def tool_answered(self, result: defs.ToolResult) -> bool:
        """tool.result from the app: hand it to whoever is waiting. False when nobody was."""
        return self.running.calls.answered(result)

    # ── the log ─────────────────────────────────────────────────────────────────

    # encode() here, at the one call site: the ledger's CallLog takes the wire's own dict, masks
    # it, publishes it to every live reader and seals the log after call.summary.
    async def emit(self, type: str, event: WireModel, ephemeral: bool | None = None) -> Entry:
        """Append the entry, then hand the very same entry to everyone reading this call."""
        entry = await self._log.append(type, encode(event), ephemeral)
        for watcher in list(self._watchers):
            try:
                await watcher(entry)
            except Exception:  # a reader that went away must never break the call's log
                self._watchers.remove(watcher)
        return entry

    @property
    def speech_now(self) -> str:
        """The speech every entry of the reply in flight is filed under."""
        reply = self.turns.reply
        return reply.speech_id if reply is not None else self._a_speech_id()

    def _a_speech_id(self) -> str:
        """The id that joins a turn to its transcripts, its metrics and its tool calls."""
        self._speeches += 1
        return f"sp_{self._speeches}"


# The class's own file, as the declaration carried it. A class that ships none has an empty
# knowledge block, which sends nothing at all.
def _the_file_it_ships_with(config: AgentConfig) -> str:
    """The text of the file this agent knows by heart, or nothing."""
    return config.knowledge or ""

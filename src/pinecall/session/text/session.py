"""One text call: a livekit AgentSession runs the model, and this writes the log from its path."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from livekit.agents import llm as agents
from livekit.agents.voice import AgentSession

from pinecall.log import NOTHING_SAID, hashed_prompt
from pinecall.log.entry import Entry
from pinecall.log.logs import CallLog
from pinecall.providers import prices
from pinecall.providers.models import Chat
from pinecall.session import clock
from pinecall.session.declaring import declared
from pinecall.session.scoring import Scorer, unjudged
from pinecall.session.text.agent import TextAgent, remembered
from pinecall.session.text.measure import Reply, usage_rows
from pinecall.session.text.running import Running
from pinecall.session.text.turns import Turns
from pinecall.types import AgentConfig, CallContext, ToolSpec
from pinecall_protocol import WireModel, defs, encode
from pinecall_protocol.commands import StateSet
from pinecall_protocol.defs import EndedBy, Supervisor
from pinecall_protocol.events import (
    CallEnded,
    CallScore,
    CallStarted,
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


# Two regions, because they age differently and livekit keeps them apart: the static one is the
# app's instructions and IS the Agent's `instructions` — the cached prefix Anthropic's breakpoint
# lands on — while the view is rendered from state and is appended to the request's context per
# turn, after the history, so a changed view never invalidates the prefix.
@dataclass(frozen=True)
class Prompt:
    """The system prompt as prompt.set builds it: the cached region, and the one that moves."""

    static: str = ""
    view: str = ""


class TextSession:
    """One conversation in text: no audio, no room, and the same log a phone call writes."""

    def __init__(
        self,
        context: CallContext,
        config: AgentConfig,
        log: CallLog,
        llm: Chat,
        score: Scorer = unjudged,
    ) -> None:
        self.context = context
        self.config = config
        self.llm = llm
        self._score = score
        # Where the session was when the app's next state.set arrives: running.py stamps a tool
        # here, receives() stamps an event, and set_state takes it and clears it.
        self.cause: StateCause | None = None
        # Who at the desk is holding this thread, when somebody is: it has to survive between two
        # commands, because a takeover and the release that answers it are minutes apart.
        self.taken_by: Supervisor | None = None
        self._log = log
        self._watchers: list[Watcher] = []
        self._prompt = Prompt(static=config.instructions or "")
        # Everything the app declared is visible until a tools.set narrows it: livekit only runs
        # a tool it holds, so the declaration IS the registration.
        self._visible: tuple[ToolSpec, ...] = tuple(config.tools_by_name.values())
        self._state: dict[str, Any] = {}
        self._speeches = 0
        self._started_at = time.time()
        self._ended = False
        self.turns = Turns(self)
        self.running = Running(self, config)
        self.text_agent = TextAgent(
            instructions=self._prompt.static,
            tools=declared(self._visible, self.running.ran),
            llm=llm,
            writer=self.turns,
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

    @property
    def view(self) -> str:
        """The dynamic region, read per request so it never enters the cached instructions."""
        return self._prompt.view

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
        # `from` is a keyword, so this one event is built from the wire's own key names.
        started: dict[str, Any] = {
            "channel": self.context.channel,
            "direction": self.context.direction,
            "from": self.context.caller,
            "to": self.agent,
            "caller": None,
            "started_at": self._started_at,
        }
        await self.emit("call.started", CallStarted.model_validate(started))

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
        await self.turns.answer(speech, arrived, said=text)

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

    async def set_prompt(self, region: defs.PromptRegion, text: str) -> Entry:
        """prompt.set: one region rewritten. The text stays out of the log; its hash goes in."""
        self._prompt = (
            Prompt(static=text, view=self._prompt.view)
            if region == "static"
            else Prompt(static=self._prompt.static, view=text)
        )
        # The static region IS livekit's instructions; the view is read per request, in llm_node.
        if region == "static":
            await self.text_agent.update_instructions(text)
        return await self.emit(
            "prompt.changed",
            PromptChanged(region=region, hash=hashed_prompt(text), chars=len(text)),
        )

    async def set_tools(self, tools: Sequence[defs.ToolSpec]) -> Entry:
        """tools.set: the subset of the declared tools the model may see in this state."""
        by_name = self.config.tools_by_name
        self._visible = tuple(by_name[tool.name] for tool in tools if tool.name in by_name)
        visible: list[agents.Tool | agents.Toolset] = list(
            declared(self._visible, self.running.ran)
        )
        await self.text_agent.update_tools(visible)
        return await self.emit(
            "tools.changed", ToolsChanged(visible=[tool.name for tool in self._visible])
        )

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

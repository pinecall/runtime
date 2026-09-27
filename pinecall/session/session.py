"""One call on livekit's AgentSession, spoken or written, and every entry the call writes."""

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Mapping, Sequence
from typing import Never, override

from livekit import rtc
from livekit.agents import (
    NOT_GIVEN,
    AgentStateChangedEvent,
    APIStatusError,
    CloseEvent,
    CloseReason,
    ConversationItemAddedEvent,
    NotGivenOr,
    RunContext,
    SessionUsageUpdatedEvent,
    StopResponse,
    ToolError,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
    get_job_context,
    inference,
    llm,
    metrics,
    stt,
    tts,
    utils,
)
from livekit.agents import ErrorEvent as ComponentFailed
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.metrics.base import AvatarMetrics
from livekit.agents.types import TimedString
from livekit.agents.voice import Agent, AgentSession, ModelSettings, STTContextOptions
from livekit.agents.voice import text_transforms as transforms
from livekit.agents.voice.agent_session import DEFAULT_TTS_TEXT_TRANSFORMS
from livekit.agents.voice.turn import InterruptionOptions, TurnHandlingOptions
from pydantic import BaseModel

from pinecall.domain.errors import DeclarationRefused, NotAllowed, PinecallError
from pinecall.domain.types import AgentConfig, JsonObject
from pinecall.log.log import started_entry
from pinecall.providers.build import Running, llm_of, stt_of, tts_of
from pinecall.providers.keys import Pipeline
from pinecall.session import prompt, room, tools
from pinecall.session.call import Call, ToolUse
from pinecall.session.prompt import Blocks
from pinecall.wire import events as wire
from pinecall.wire import metrics as measured
from pinecall.wire.commands import (
    AgentReply,
    AgentSay,
    CallAttention,
    CallCallback,
    CallEvent,
    CallHangup,
    CallHold,
    CallLog,
    CallTransfer,
    CallUnhold,
    EndVerb,
    PromptSet,
    ReleaseVerb,
    SayVerb,
    StateSet,
    SupervisorVerb,
    TakeoverVerb,
    ToolsSet,
    TransferVerb,
    WhisperVerb,
)
from pinecall.wire.frames import WireModel
from pinecall.wire.parts import EndedBy, EndReason, Supervisor, ToolResult

logger = logging.getLogger(__name__)

# ── how a turn is taken ──

# The local end-of-turn model: left unset, livekit may pick the hosted one and send the
# caller's words to a cloud.
LOCAL_TURN_VERSION: inference.TurnDetectorVersions = "v1-mini"
# livekit counts no words by default; one word is as often a cough or an echo.
MIN_WORDS = 2
# Silence before an interruption is judged false: livekit's 2 s sound like a dropped call.
FALSE_INTERRUPTION_TIMEOUT_S = 1.0
# livekit's max_tool_steps: the step after a tool round is forced to answer in words, so the agent
# does not keep talking after it answered.
ONE_ANSWER_PER_TOOL = 1

# A turn made of these alone is somebody agreeing, and does not take the floor. livekit's own
# backchannel detector is hosted only.
BACKCHANNELS = frozenset(
    {
        "aha", "ajá", "ah", "ajam", "bien", "bueno", "claro", "dale", "eh", "em", "exacto", "hm",
        "hmm", "mm", "mmm", "ok", "okay", "perfecto", "sí", "si", "vale", "ya", "yeah", "yes",
        "uh", "uhu", "uhum",
    }
)  # fmt: skip
_A_WORD = re.compile(r"[^\W_]+(?:['\u2019][^\W_]+)*")

# Keyterms are names, not sentences: longer text dilutes them. The cap is ours, since the ears'
# vendors either document none or count the terms against the model's budget.
LONGEST_TERM = 40
MOST_WORDS = 4
MOST_TERMS = 50

# ── how a call ends ──

# Errors of the request itself; 408, 429 and 5xx are transient and livekit retries them.
FOREVER = frozenset({400, 401, 402, 403, 404, 422})
# The WebSocket close for a policy violation: a vendor that refuses a voice or a key sends it,
# and livekit's 4xx check misses it.
POLICY_VIOLATION = 1008

# livekit's close reason misreads what our code knows: a cold transfer reads as the caller
# hanging up, end_call as a drain. The reason recorded wins; this is what is left.
HOW_IT_ENDED: dict[CloseReason, tuple[EndReason, EndedBy]] = {
    CloseReason.PARTICIPANT_DISCONNECTED: ("caller_hung_up", "caller"),
    CloseReason.ERROR: ("error", "platform"),
    CloseReason.JOB_SHUTDOWN: ("drained", "platform"),
    CloseReason.USER_INITIATED: ("drained", "platform"),
    CloseReason.TASK_COMPLETED: ("agent_hung_up", "agent"),
}
A_PLATFORM_ERROR: tuple[EndReason, EndedBy] = ("error", "platform")
NOTHING_SAID = "no reply"
# What the session waits for the log after the call ended: past it, the gateway's reaper
# seals what this could not.
SEAL_S = 20.0

# The goodbye comes in the same turn as the call to end: a reply generated after it says odd
# things, so nothing is generated.
SAY_GOODBYE_FIRST = (
    "Say your goodbye in the same reply in which you call this, before the call: nothing you "
    "say after it is heard, and nothing is generated for you."
)
END_CALL = "end_call"

# ── what a supervisor's words do to the model ──

# Written into the history and read as the next turn's instructions. It claims precedence,
# since the stage instructions come after the history and the model would follow them instead.
A_WHISPER = (
    "A human supervisor is telling you this, and the caller cannot hear it: {text} "
    "This order comes from the supervisor and takes precedence over the stage instructions "
    "that follow it: do it in your very next sentence, before anything else you were going to "
    "say, and only then go on. Never mention the supervisor or this note."
)
# The agent did not hear the supervisor, so it must not guess what was said.
A_RELEASE = (
    "A human supervisor spoke with the caller for a moment; you did not hear it. "
    "Do not guess what was said. Resume by offering to continue with what is still pending."
)
ALREADY_HELD = "supervisor.verb: {id} already holds the line; they release it, or nobody does"
NOBODY_HOLDS = "supervisor.verb: nobody holds the line, so there is nothing to release"
ALREADY_WAITING = "call.attention: the caller is already waiting for a person"
# The app's tool timeout must outlast the wait it asks for; the runtime does not extend it.
NOBODY_TOOK_IT = "nobody took the line within {wait_s:g}s"
NOT_HERE = "{command} is not something this call does"

# ── the time a call is given ──

# A limit under two minutes is told at its half instead.
WARNED_BEFORE_S = 60
CLOSING = (
    "The call reaches its time limit in about a minute. Bring it to a close now: answer what is "
    "pending in a sentence, tell the caller the call has to end soon, and say goodbye."
)

type Heard = AsyncIterator[stt.SpeechEvent]
type Declared = list[llm.Tool | llm.Toolset]
# What a session built and measures: the model, and on a voice call the ears and the voice.
type Built = tuple[llm.LLM[Never] | stt.STT[Never] | tts.TTS[Never], ...]
type Thought = AsyncIterator[llm.ChatChunk | str]
type Words = AsyncIterator[str | TimedString]


class Prompted(Agent):
    """livekit's Agent reading the call's prompt blocks, lookups and tools."""

    def __init__(
        self, live: AgentSession[None], blocks: Blocks, lookups: tools.Lookups, declared: Declared
    ) -> None:
        """The agent with the static blocks as its instructions and every tool of the call."""
        super().__init__(instructions=blocks.instructions, tools=declared)  # pyright: ignore[reportUnknownMemberType]
        self.live = live
        self.call = lookups.call
        self.blocks = blocks
        self.lookups = lookups

    # Runs before the request and livekit times it, so the lookups here are the ones already
    # running (started on an interim) or quick ones. No speech handle exists yet.
    @override
    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """Collect this turn's lookups and write the ones that did not run."""
        for skipped in await self.lookups.turn_ended(new_message.text_content or "", None):
            self.call.writing.write("error", skipped)

    # Lookups and dynamic blocks go into this request only, so the cached prefix (instructions
    # and history) stays the same bytes from turn to turn.
    @override
    async def llm_node(
        self, chat_ctx: llm.ChatContext, tools: list[llm.Tool], model_settings: ModelSettings
    ) -> Thought:
        """The model run on the history, this turn's lookups, and the dynamic blocks."""
        asked = prompt.request(chat_ctx, self.blocks, self.lookups.items)
        async for chunk in Agent.default.llm_node(self, asked, tools, model_settings):
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
                self.call.writing.write("agent.transcript", _said(self.live, delta))
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
        said = event.alternatives[0].text if event.alternatives else ""
        return self.call.agent_speaking and is_a_backchannel(said)


class Session:
    """One call's AgentSession and the call it writes: its events, commands, time and end."""

    def __init__(self, live: AgentSession[None], built: Built, lookups: tools.Lookups) -> None:
        """The session of a call nobody has spoken on, its agent holding every tool."""
        self.call = lookups.call
        self.live = live
        self.built = built
        self.lookups = lookups
        self.blocks = Blocks(self.call.config.prompt, self.call.config.knowledge or "")
        self.agent = Prompted(live, self.blocks, lookups, self._declared())
        self.started_at = time.time()
        # Why the call ended, when our code knows: it wins over livekit's close reason.
        self.ended: tuple[EndReason, EndedBy] | None = None
        self.closed_for: CloseReason | None = None
        self.closed = False
        self.dead_end = False
        self.language: str | None = None
        self.usage: list[measured.ModelUsage] = []
        self.held = False
        self.waiting: asyncio.Task[None] | None = None
        # Given at start; a written call has neither.
        self.room: room.Room | None = None
        self.hold: room.HoldMusic | None = None

    # ── the session's life ──

    # call.started comes first, so it is written before livekit starts and says anything.
    async def start(
        self, *, where: room.Room | None = None, hold: room.HoldMusic | None = None
    ) -> None:
        """Open a new call: its first entries, the session on its room or headless, the greeting."""
        self.room = where
        self.hold = hold
        self.call.writing.open()
        context = self.call.context
        door = context.route.number or self.call.config.slug
        started = wire.CallStarted.model_validate(started_entry(context, door, self.started_at))
        await self.call.writing.write("call.started", started)
        knowledge = prompt.knowledge_changed(self.blocks)
        if knowledge is not None:
            await self.call.writing.write("prompt.changed", knowledge)
        await self._opened(llm.ChatContext.empty())
        greeting = prompt.greeting_for(self.call.config.greeting, context.run)
        if greeting is not None and greeting.say is not None:
            self.live.say(greeting.say, allow_interruptions=_given(greeting.allow_interruptions))
        elif greeting is not None and greeting.reply is not None:
            interruptible = _given(greeting.allow_interruptions)
            self.live.generate_reply(instructions=greeting.reply, allow_interruptions=interruptible)

    # The caller is in the middle of the conversation: no call.started, no greeting.
    async def resume(self, history: llm.ChatContext) -> None:
        """Open a call taken up again, with the history it had."""
        self.call.writing.open()
        await self._opened(history)

    def hang_up(self, reason: EndReason, by: EndedBy = "agent", *, at_once: bool = False) -> None:
        """End the call for this reason: the session, and the job that runs it."""
        self.ended = (reason, by)
        self.live.shutdown(drain=not at_once)
        job = get_job_context(required=False)
        if job is not None:
            job.shutdown(reason=reason)

    # The session closes first: its close writes the last turn, which comes before call.ended.
    # The log is flushed before the seal, since the gateway reads the call back from it.
    async def close(self) -> None:
        """Write call.ended and hand the rest to the seal, within the seal's budget."""
        if self.closed:
            return
        self.closed = True
        await self.live.aclose()
        for name in LISTENED:
            self.live.off(name, self._heard)  # pyright: ignore[reportUnknownMemberType]
        for component in self.built:
            component.off("metrics_collected", self._measured)  # pyright: ignore[reportUnknownMemberType]
        if self.waiting is not None:
            await utils.aio.cancel_and_wait(self.waiting)
        await self.lookups.close()
        reason, by = self._how_it_ended()
        ended_at = time.time()
        ended = wire.CallEnded(
            reason=reason, ended_by=by, ended_at=ended_at, duration_s=ended_at - self.started_at
        )
        try:
            await self.call.writing.write("call.ended", ended)
            await self.call.writing.flushed(SEAL_S)
            await self.call.platform.seal(self.usage, self.call.last_said or NOTHING_SAID)
        finally:
            await self.call.writing.close(SEAL_S)

    # The limit holds while a supervisor has the line; only the warning is skipped, since a
    # generated turn would talk over them.
    async def keep_time(self, limit_s: int, *, exhausted: wire.CreditsExhausted | None) -> None:
        """Warn the agent before the limit and end the call at it; nothing when there is none."""
        if limit_s == 0:
            return
        warned_at = limit_s - WARNED_BEFORE_S if limit_s >= 2 * WARNED_BEFORE_S else limit_s / 2
        await asyncio.sleep(warned_at)
        if not self.call.a_person_has_the_line:
            self.live.generate_reply(instructions=CLOSING)
        await asyncio.sleep(limit_s - warned_at)
        if exhausted is not None:
            await self.call.writing.write("credits.exhausted", exhausted)
        self.hang_up("timeout", "platform")

    # ── the app's commands ──

    async def apply(self, command: WireModel) -> None:
        """What one command of the app does to this call."""
        if await self._written(command) or await self._spoken(command):
            return
        if isinstance(command, SupervisorVerb):
            await self.supervise(command)
            return
        await self._in_the_room(command)

    async def _written(self, command: WireModel) -> bool:
        match command:
            case PromptSet():
                await self.set_prompt(command.name, command.text)
            case ToolsSet():
                await self.call.set_tools(command)
            case StateSet():
                await self.call.set_state(command)
                self._tell_the_ears(command.state)
            case CallEvent():
                await self.call.receives(command.name, command.data, source="app")
            case CallLog():
                await self.call.log_custom(command)
            case CallCallback():
                await self.call.call_back(command)
            case _:
                return False
        return True

    async def _spoken(self, command: WireModel) -> bool:
        match command:
            case AgentSay():
                self.live.say(command.text, allow_interruptions=_given(command.allow_interruptions))
            case AgentReply():
                self.live.generate_reply(
                    instructions=command.instructions,
                    allow_interruptions=_given(command.allow_interruptions),
                )
            case CallHangup():
                self.hang_up("agent_hung_up")
            case CallHold():
                await self.hold_the_line()
            case CallUnhold():
                await self.give_the_line_back()
            case CallAttention():
                await self.ask_for_a_person(command)
            case _:
                return False
        return True

    # Only a change of the static text rewrites the instructions: identical bytes would still
    # cost a cache write. The dynamic blocks are read by each request.
    async def set_prompt(self, name: str, text: str) -> None:
        """Rewrite one block; the log keeps its hash."""
        if self.blocks.set(name, text):
            await self.agent.update_instructions(self.blocks.instructions)
        changed = wire.PromptChanged(name=name, hash=prompt.hashed(text), chars=len(text))
        await self.call.writing.write("prompt.changed", changed)

    # ── the line ──

    async def hold_the_line(self) -> None:
        """The agent neither speaks nor hears, the melody plays, and the log says so."""
        if self.held:
            return
        self.held = True
        await _silence(self.live)
        if self.hold is not None:
            self.hold.began()
        await self.call.writing.write("call.line", wire.CallLine(held=True, muted=False))

    async def give_the_line_back(self, *, to_the_agent: bool = True) -> None:
        """The melody stops and, unless a person keeps the line, the agent hears and speaks."""
        if not self.held:
            return
        self.held = False
        if self.hold is not None:
            self.hold.ended()
        if to_the_agent:
            _hearing_again(self.live)
        await self.call.writing.write("call.line", wire.CallLine(held=False, muted=False))

    async def ask_for_a_person(self, wanted: CallAttention) -> None:
        """Hold the caller for a supervisor, for as long as the app asked, once at a time."""
        if self.call.waiting_for_a_person:
            raise DeclarationRefused(ALREADY_WAITING)
        self.call.waiting_for_a_person = True
        asked = wire.AttentionRequested(reason=wanted.reason, wait_s=wanted.wait_s)
        await self.call.writing.write("attention.requested", asked)
        await self.hold_the_line()
        self.waiting = asyncio.create_task(self._nobody_came(wanted.wait_s))

    # ── a supervisor ──

    async def supervise(self, command: SupervisorVerb) -> None:
        """One of a supervisor's six verbs, written before it acts."""
        by, verb = command.by, command.verb
        match verb:
            case SayVerb():
                await self.call.writing.write(
                    "supervisor.said", wire.SupervisorSaid(by=by, text=verb.text)
                )
                self.live.say(verb.text, allow_interruptions=True)
            case WhisperVerb():
                await self.call.writing.write(
                    "supervisor.whispered", wire.SupervisorWhispered(by=by, text=verb.text)
                )
                note = A_WHISPER.format(text=verb.text)
                await self._noted(note)
                if self.call.taken_by is None:
                    self.live.generate_reply(instructions=note)
            case TakeoverVerb():
                await self._take_over(by)
            case ReleaseVerb():
                await self._release(by)
            case EndVerb():
                await self.call.writing.write(
                    "supervisor.ended", wire.SupervisorEnded(by=by, reason=verb.reason)
                )
                self.hang_up("supervisor_ended", "supervisor", at_once=True)
            case TransferVerb():
                await self._in_the_room(verb, by=by)

    async def _take_over(self, by: Supervisor) -> None:
        if self.call.taken_by is not None:
            raise DeclarationRefused(ALREADY_HELD.format(id=self.call.taken_by.id))
        await self.call.writing.write("supervisor.took_over", wire.SupervisorTookOver(by=by))
        if self.call.waiting_for_a_person:
            await self._answered(wire.AttentionAnswered(ok=True, by=by))
        await self.give_the_line_back(to_the_agent=False)
        await _silence(self.live)
        self.call.taken_by = by

    async def _release(self, by: Supervisor) -> None:
        if self.call.taken_by is None:
            raise DeclarationRefused(NOBODY_HOLDS)
        await self.call.writing.write("supervisor.released", wire.SupervisorReleased(by=by))
        _hearing_again(self.live)
        self.call.taken_by = None
        await self._noted(A_RELEASE)
        self.live.generate_reply(instructions=A_RELEASE)

    async def _nobody_came(self, wait_s: float) -> None:
        await asyncio.sleep(wait_s)
        failed = NOBODY_TOOK_IT.format(wait_s=wait_s)
        await self._answered(wire.AttentionAnswered(ok=False, by=None, error=failed), waited=True)
        await self.give_the_line_back()

    async def _answered(self, answer: wire.AttentionAnswered, *, waited: bool = False) -> None:
        self.call.waiting_for_a_person = False
        if self.waiting is not None and not waited:
            self.waiting.cancel()
        self.waiting = None
        await self.call.writing.write("attention.answered", answer)

    async def _in_the_room(self, command: WireModel, *, by: Supervisor | None = None) -> None:
        if self.room is None:
            raise NotAllowed(NOT_HERE.format(command=type(command).__name__))
        if isinstance(command, TransferVerb | CallTransfer):
            await self._transfer(self.room, CallTransfer(to=command.to, mode=command.mode), by=by)
            return
        await self.room.apply(command)

    # A transfer however it was asked has one outcome entry; a supervisor's is written first,
    # with the mode resolved. A cold one removes the caller, which livekit reads as a hang-up.
    async def _transfer(
        self, where: room.Room, wanted: CallTransfer, *, by: Supervisor | None
    ) -> None:
        if by is not None:
            mode = await where.mode_of(wanted)
            asked = wire.SupervisorTransferred(by=by, to=wanted.to, mode=mode)
            await self.call.writing.write("supervisor.transferred", asked)
            wanted = CallTransfer(to=wanted.to, mode=mode)
        transferred = await where.transfer(wanted, self.live)
        await self.call.writing.write("call.transferred", transferred)
        if not transferred.ok:
            return
        self.ended = ("transferred", "agent")
        if transferred.mode == "warm":
            await _silence(self.live)
            where.when_the_person_leaves(
                f"{room.LEG_PREFIX}{wanted.to}", lambda: self.hang_up("transferred")
            )

    # ── what the session tells us ──

    def _heard(self, event: object) -> None:
        match event:
            case UserInputTranscribedEvent():
                self._transcribed(event)
            case UserStateChangedEvent():
                self.call.writing.write("user.state", wire.UserStateChanged(state=event.new_state))
            case AgentStateChangedEvent():
                self.call.agent_speaking = event.new_state == "speaking"
                if self.hold is not None:
                    self.hold.floor(speaking=self.call.agent_speaking)
                self.call.writing.write(
                    "agent.state", wire.AgentStateChanged(state=event.new_state)
                )
            case ConversationItemAddedEvent():
                self._turn(event.item)
            case SessionUsageUpdatedEvent():
                self.usage = [_usage_of(used) for used in event.usage.model_usage]
            case ComponentFailed():
                self._failed(event)
            case CloseEvent():
                self.closed_for = event.reason
            case _:
                return

    # Interims also start the lookups, so recall and search run while the caller still talks.
    def _transcribed(self, event: UserInputTranscribedEvent) -> None:
        self.language = event.language or self.language
        said = wire.UserTranscript(
            text=event.transcript, final=event.is_final, language=event.language
        )
        self.call.writing.write("user.transcript", said)
        if not event.is_final:
            self.lookups.heard_so_far(event.transcript)

    # Metrics are whole by the time an item is added, so the turns are written here.
    def _turn(self, item: object) -> None:
        if not isinstance(item, llm.ChatMessage) or item.role not in {"user", "assistant"}:
            return
        speech = self.live.current_speech.id if self.live.current_speech else item.id
        text = item.text_content or ""
        if item.role == "assistant":
            self.call.turns += 1
            self.call.last_said = text or self.call.last_said
            agent = wire.AgentTurnEnded(
                speech_id=speech,
                item_id=item.id,
                text=text,
                interrupted=item.interrupted,
                metrics=measured.AgentTurnMetrics.model_validate(dict(item.metrics)),
            )
            self.call.writing.write("turn.agent", agent)
            return
        eou = _end_of_utterance(item.metrics, speech)
        if eou is not None:
            self.call.writing.write("metrics.eou", eou)
        user = wire.UserTurnEnded(
            speech_id=speech,
            item_id=item.id,
            text=text,
            language=self.language,
            transcript_confidence=item.transcript_confidence,
            metrics=measured.UserTurnMetrics.model_validate(dict(item.metrics)),
        )
        self.call.writing.write("turn.user", user)

    # livekit closes only after three unrecoverable errors. A refusal that can never succeed (a
    # bad key, a voice that does not exist) ends the call on the first.
    def _failed(self, event: ComponentFailed) -> None:
        if self.dead_end:
            return
        said = str(event.error)
        wrapped = getattr(event.error, "error", None)
        if not (isinstance(wrapped, APIStatusError) and _forever(wrapped)):
            recoverable = bool(getattr(event.error, "recoverable", False))
            failed = wire.ErrorEvent(code="component_failed", message=said, recoverable=recoverable)
            self.call.writing.write("error", failed)
            return
        self.dead_end = True
        dead = wire.ErrorEvent(code="component_dead_end", message=said, recoverable=False)
        self.call.writing.write("error", dead)
        self.hang_up("error", "platform")

    # Deferred a tick: livekit stamps the speech id on the same object from a listener of its
    # own, and the order of listeners is not defined. Streaming ears report usage at their own
    # cadence; those ticks ride the stream and the total reaches the summary.
    def _measured(self, block: metrics.AgentMetrics) -> None:
        asyncio.get_running_loop().call_soon(self._write_block, block)

    def _write_block(self, block: metrics.AgentMetrics) -> None:
        written = _block_of(block)
        if written is None:
            return
        kind, model = written
        tick = isinstance(block, metrics.STTMetrics) and block.streamed and not block.acquire_time
        self.call.writing.write(kind, model, ephemeral=True if tick else None)

    # ── what the agent is handed ──

    def _declared(self) -> list[llm.Tool | llm.Toolset]:
        config = self.call.config
        declared: list[llm.Tool | llm.Toolset] = [
            *tools.declared(config.tools, self._run_app_tool),
            *tools.declared(tools.platform_tools(config), self._run_lookup),
        ]
        if config.hangup is not None:
            declared.append(
                EndCallTool(
                    extra_description=f"{config.hangup.when}\n{SAY_GOODBYE_FIRST}".strip(),
                    # Hidden while the agent greets, or models hang up before the caller speaks.
                    ignore_on_enter=True,
                    # Deleting the room ends the caller's SIP leg, or they hear a silent line.
                    delete_room=True,
                    end_instructions=None,
                    on_tool_called=self._ended_by_the_model,
                    on_tool_completed=_nothing_after,
                )
            )
        return declared

    # The model's words and its tool call arrive in one answer: its announcement plays before the
    # tool runs. The read-back is said inside the tool and awaited, so it is in the history the
    # reply is built from, or the model says it again in its own words.
    async def _run_app_tool(self, use: ToolUse, context: RunContext[None]) -> str:
        spec = self.call.config.tools_by_name[use.name]
        await tools.admitted(self.call, use.name)
        await context.wait_for_playout()
        # The gateway writes the tool's round trip: the turn that called it is in the log first.
        await self.call.writing.flushed(SEAL_S)
        self.call.cause = wire.StateCauseTool(kind="tool", tool=use.name, call_id=use.call_id)
        try:
            async with self._music():
                result = await self.call.platform.tool(use, context.speech_handle.id)
        # The gateway's refusal (the app gone, a guard) is the model's to read, in its own turn.
        except PinecallError as refused:
            raise ToolError(str(refused)) from refused
        finally:
            if self.call.cause == wire.StateCauseTool(
                kind="tool", tool=use.name, call_id=use.call_id
            ):
                self.call.cause = None
        text = tools.result_text(result)
        if result.error is not None:
            raise ToolError(text)
        if spec.confirm:
            await self.live.say(tools.read_back(spec.confirm, use.arguments, result))
        # A supervisor took the line while the tool ran: the result stays, the reply does not.
        if self.call.a_person_has_the_line:
            raise StopResponse
        return text

    async def _run_lookup(self, use: ToolUse, _context: RunContext[None]) -> str:
        return await self.lookups.called(use)

    # end_call closes the session as livekit's own initiative, which reads as a drain: the reason
    # is written down first, and the tool with it, since it never reaches the app.
    async def _ended_by_the_model(self, event: llm.Toolset.ToolCalledEvent) -> None:
        self.ended = ("agent_hung_up", "agent")
        context: RunContext[None] = event.ctx  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        call_id = context.function_call.call_id
        speech = context.speech_handle.id
        called = wire.ToolCall(call_id=call_id, name=END_CALL, arguments={}, speech_id=speech)
        await self.call.writing.write("tool.call", called)
        await self.call.writing.write("tool.result", ToolResult(call_id=call_id, name=END_CALL))

    @contextlib.asynccontextmanager
    async def _music(self) -> AsyncGenerator[None]:
        if self.hold is not None:
            self.hold.began()
        try:
            yield
        finally:
            if self.hold is not None:
                self.hold.ended()

    async def _opened(self, history: llm.ChatContext) -> None:
        for name in LISTENED:
            self.live.on(name, self._heard)  # pyright: ignore[reportUnknownMemberType]
        on = NOT_GIVEN if self.room is None else self.room.room
        await self.live.start(self.agent, room=on)  # pyright: ignore[reportUnknownMemberType]
        for component in self.built:
            component.on("metrics_collected", self._measured)  # pyright: ignore[reportUnknownMemberType]
        told = history.copy()
        told.items[:0] = list(tools.date_pair(self.call.context.today))
        await self.agent.update_chat_ctx(told, exclude_invalid_function_calls=False)

    # A note joins the history as livekit appends: copy, add, update. Never a static block,
    # which would spend the cache.
    async def _noted(self, note: str) -> None:
        told = self.agent.chat_ctx.copy()
        told.add_message(role="system", content=note)
        await self.agent.update_chat_ctx(told, exclude_invalid_function_calls=False)

    # Names the state holds (a patient's) join the declared words; livekit replaces the
    # session's keyterms in place.
    def _tell_the_ears(self, state: JsonObject) -> None:
        ears = next((one for one in self.built if isinstance(one, stt.STT)), None)
        if ears is not None and ears.capabilities.keyterms:
            self.live.update_options(keyterms=keyterms(self.call.config, state))

    def _how_it_ended(self) -> tuple[EndReason, EndedBy]:
        if self.ended is not None:
            return self.ended
        return A_PLATFORM_ERROR if self.closed_for is None else HOW_IT_ENDED[self.closed_for]


# ── building one ──

# What a spoken turn waits for recall and search after the caller stops, and a written one.
VOICE_LOOKUP_MS = 250
TEXT_LOOKUP_MS = 3000

LISTENED = (
    "user_input_transcribed",
    "user_state_changed",
    "agent_state_changed",
    "conversation_item_added",
    "session_usage_updated",
    "error",
    "close",
)


def spoken(call: Call, stages: Pipeline, *, lookup_ms: int = VOICE_LOOKUP_MS) -> Session:
    """A voice call: the three stages built for it, the turn taken as a phone line needs."""
    thinking, ears, voice = (
        llm_of(stages.llm),
        stt_of(stages.stt, call.config.turn),
        tts_of(stages.tts),
    )
    live: AgentSession[None] = AgentSession(
        llm=thinking,
        stt=ears,
        tts=voice,
        turn_handling=_spoken_turns(call.config, ends_the_turn=stages.stt.ends_the_turn),
        # Word timings reach transcription_node only from an aligned voice.
        use_tts_aligned_transcript=True,
        tts_text_transforms=_transforms(call.config),
        stt_context_options=_context(call.config, ears),
        max_tool_steps=ONE_ANSWER_PER_TOOL,
    )
    return Session(
        live, (thinking, ears, voice), tools.Lookups(call, call.platform.lookup, lookup_ms)
    )


# A written call has no ears and no voice, so its text is neither paced nor billed as speech;
# its turns are taken by hand, one message at a time.
def written(call: Call, thinking: Running, *, lookup_ms: int = TEXT_LOOKUP_MS) -> Session:
    """A written call: the same session with the model alone."""
    model = llm_of(thinking)
    live: AgentSession[None] = AgentSession(
        llm=model,
        turn_handling={"turn_detection": "manual", "preemptive_generation": {"enabled": False}},
        max_tool_steps=ONE_ANSWER_PER_TOOL,
    )
    return Session(live, (model,), tools.Lookups(call, call.platform.lookup, lookup_ms))


def is_a_backchannel(said: str) -> bool:
    """Whether every word said is somebody agreeing."""
    words = [word.lower() for word in _A_WORD.findall(said)]
    return bool(words) and all(word in BACKCHANNELS for word in words)


# The declared words first, so the cap drops the names found in the state before them. One
# level deep catches `patient = {name, phone}`; a term needs a letter, which leaves out numbers.
def keyterms(config: AgentConfig, state: JsonObject) -> list[str]:
    """The words the ears are told to expect: the agent's own, then names the state holds."""
    found: list[object] = []
    for value in state.values():
        found += list(value.values()) if isinstance(value, dict) else [value]
    names = [name for name in (_a_name(one) for one in found) if name]
    return list(dict.fromkeys(term for term in (*config.hears, *names) if term))[:MOST_TERMS]


# ── helpers ──


# The parameter replaces livekit's defaults, so they come first and the tenant's words after.
def _transforms(config: AgentConfig) -> NotGivenOr[Sequence[transforms.TextTransforms]]:
    if not config.says:
        return NOT_GIVEN
    return [*DEFAULT_TTS_TEXT_TRANSFORMS, transforms.replace(dict(config.says))]


def _context(config: AgentConfig, ears: stt.STT[Never]) -> NotGivenOr[STTContextOptions]:
    if not config.hears or not ears.capabilities.keyterms:
        return NOT_GIVEN
    return {"keyterms": keyterms(config, {})}


# Interruptions are judged by the local VAD: livekit's adaptive detector streams the caller's
# audio to its cloud. A false interruption is not resumed: livekit replays the whole sentence.
# No endpointing here: the agent's own already reaches the ears, and both would wait twice.
def _spoken_turns(config: AgentConfig, *, ends_the_turn: bool) -> TurnHandlingOptions:
    declared = config.turn.min_interruption_words if config.turn else None
    interruption: InterruptionOptions = {
        "min_words": MIN_WORDS if declared is None else declared,
        "mode": "vad",
        "false_interruption_timeout": FALSE_INTERRUPTION_TIMEOUT_S,
        "resume_false_interruption": False,
    }
    return {
        # Ears that end the turn themselves decide it; the local detector stacked on them waits
        # its whole delay after a pause in the middle of a sentence.
        "turn_detection": "stt"
        if ends_the_turn
        else inference.TurnDetector(version=LOCAL_TURN_VERSION),
        # A reply started inside a tool's window, on a context without its result, answers its
        # own question.
        "preemptive_generation": {"enabled": False},
        "interruption": interruption,
    }


def _given[T](value: T | None) -> NotGivenOr[T]:
    return NOT_GIVEN if value is None else value


def _said(live: AgentSession[None], delta: str | TimedString) -> wire.AgentTranscript:
    speech = live.current_speech.id if live.current_speech else ""
    timed: dict[str, float] = {}
    if isinstance(delta, TimedString):
        if utils.is_given(delta.start_time):
            timed["start"] = delta.start_time
        if utils.is_given(delta.end_time):
            timed["end"] = delta.end_time
    return wire.AgentTranscript(speech_id=speech, text=str(delta), final=False, **timed)


# interrupt() raises when nothing is playing or the session has stopped.
async def _silence(live: AgentSession[None]) -> None:
    with contextlib.suppress(RuntimeError):
        await live.interrupt(force=True)
    live.output.set_audio_enabled(False)
    live.input.set_audio_enabled(False)


# The ears first, then the voice, so the agent never speaks before it can hear.
def _hearing_again(live: AgentSession[None]) -> None:
    live.input.set_audio_enabled(True)
    live.output.set_audio_enabled(True)


async def _nothing_after(_: llm.Toolset.ToolCompletedEvent) -> None:
    raise StopResponse


def _forever(error: APIStatusError) -> bool:
    return error.status_code == POLICY_VIOLATION or error.status_code in FOREVER


def _a_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if not name or len(name) > LONGEST_TERM or len(name.split()) > MOST_WORDS:
        return ""
    return name if any(letter.isalpha() for letter in name) else ""


# livekit's rows carry the wire's names, and livekit adds fields the wire does not know
# (`input_audio_tokens` on the ears' usage in 1.8.3). The wire refuses an unknown key, and a
# listener's exception is swallowed by livekit's emitter, so the call would lose its usage in
# silence: each row is read by the fields its wire model declares.
def _read_as[T: WireModel](model: type[T], livekits: BaseModel) -> T:
    dumped = livekits.model_dump()
    return model.model_validate(
        {name: dumped[name] for name in model.model_fields if name in dumped}
    )


def _usage_of(used: metrics.ModelUsage) -> measured.ModelUsage:
    match used:
        case metrics.LLMModelUsage():
            return _read_as(measured.LLMModelUsage, used)
        case metrics.TTSModelUsage():
            return _read_as(measured.TTSModelUsage, used)
        case metrics.STTModelUsage():
            return _read_as(measured.STTModelUsage, used)
        case metrics.InterruptionModelUsage():
            return _read_as(measured.InterruptionModelUsage, used)
        case _:
            return _read_as(measured.EOTModelUsage, used)


# Each block is read by the wire's fields, under livekit's own names.
BLOCKS: tuple[tuple[type[metrics.AgentMetrics], str, type[WireModel]], ...] = (
    (metrics.LLMMetrics, "metrics.llm", measured.LLMMetrics),
    (metrics.STTMetrics, "metrics.stt", measured.STTMetrics),
    (metrics.TTSMetrics, "metrics.tts", measured.TTSMetrics),
    (metrics.VADMetrics, "metrics.vad", measured.VADMetrics),
    (metrics.EOUMetrics, "metrics.eou", measured.EOUMetrics),
    (metrics.InterruptionMetrics, "metrics.interruption", measured.InterruptionMetrics),
    (metrics.RealtimeModelMetrics, "metrics.realtime", measured.RealtimeModelMetrics),
    (metrics.EOTInferenceMetrics, "metrics.eot", measured.EOTInferenceMetrics),
    (AvatarMetrics, "metrics.avatar", measured.AvatarMetrics),
)


def _block_of(block: metrics.AgentMetrics) -> tuple[str, WireModel] | None:
    for livekits, kind, ours in BLOCKS:
        if isinstance(block, livekits):
            return kind, _read_as(ours, block)
    return None


# livekit emits the end-of-utterance block on the session only, so it is built again from the
# user turn's report, as livekit builds it.
def _end_of_utterance(report: Mapping[str, object], speech: str) -> measured.EOUMetrics | None:
    delays = ("end_of_turn_delay", "transcription_delay", "on_user_turn_completed_delay")
    if not any(key in report for key in delays):
        return None
    return measured.EOUMetrics.model_validate(
        {
            "timestamp": time.time(),
            "end_of_utterance_delay": report.get("end_of_turn_delay", 0.0),
            "transcription_delay": report.get("transcription_delay", 0.0),
            "on_user_turn_completed_delay": report.get("on_user_turn_completed_delay", 0.0),
            "speech_id": speech,
        }
    )

"""One call on livekit's AgentSession, spoken or written, and every entry the call writes."""

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator

from livekit.agents import (
    NOT_GIVEN,
    AgentStateChangedEvent,
    APIStatusError,
    CloseEvent,
    CloseReason,
    ConversationItemAddedEvent,
    RunContext,
    SessionUsageUpdatedEvent,
    StopResponse,
    ToolError,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
    get_job_context,
    llm,
    metrics,
    stt,
    utils,
)
from livekit.agents import ErrorEvent as ComponentFailed
from livekit.agents.beta.tools import EndCallTool
from livekit.agents.voice import AgentSession
from livekit.agents.voice.room_io import RoomOptions

from pinecall.domain.errors import DeclarationRefused, NotAllowed, PinecallError
from pinecall.domain.names import JsonObject
from pinecall.log.logs import started_entry
from pinecall.providers.build import Ears, Speaking, Thinking
from pinecall.session import _prompt, room, tools
from pinecall.session._agent import CallAgent
from pinecall.session._hearing import keyterms
from pinecall.session._livekit import (
    block_of,
    end_of_utterance,
    forever,
    given_or_unset,
    hearing_again,
    nothing_after,
    silence,
    usage_of,
)
from pinecall.session._prompt import A_RELEASE, A_WHISPER, Blocks
from pinecall.session.call import CLOSING, WARNED_BEFORE_S, ToolUse
from pinecall.session.hold import HoldMusic
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
    SessionConfigure,
    StateSet,
    SupervisorVerb,
    TakeoverVerb,
    ToolsSet,
    TransferVerb,
    WhisperVerb,
)
from pinecall.wire.frames import WireModel
from pinecall.wire.parts import EndedBy, EndReason, Supervisor, ToolResult

# What a session built and measures: the model, and on a voice call the ears and the voice.
type Built = tuple[Thinking | Ears | Speaking, ...]


logger = logging.getLogger(__name__)


# livekit's max_tool_steps: the step after a tool round is forced to answer in words, so the agent
# does not keep talking after it answered.
ONE_ANSWER_PER_TOOL = 1


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


ALREADY_HELD = "supervisor.verb: {id} already holds the line; they release it, or nobody does"


NOBODY_HOLDS = "supervisor.verb: nobody holds the line, so there is nothing to release"


ALREADY_WAITING = "call.attention: the caller is already waiting for a person"


# The app's tool timeout must outlast the wait it asks for; the runtime does not extend it.
NOBODY_TOOK_IT = "nobody took the line within {wait_s:g}s"


NOT_HERE = "{command} is not something this call does"


LISTENED = (
    "user_input_transcribed",
    "user_state_changed",
    "agent_state_changed",
    "conversation_item_added",
    "session_usage_updated",
    "error",
    "close",
)


class Session:
    """One call's AgentSession and the call it writes: its events, commands, time and end."""

    def __init__(self, live: AgentSession[None], built: Built, lookups: tools.Lookups) -> None:
        """The session of a call nobody has spoken on, its agent holding every tool."""
        self.call = lookups.call
        self.live = live
        self.built = built
        self.lookups = lookups
        self.blocks = Blocks(self.call.config.prompt, self.call.config.knowledge or "")
        self.agent = CallAgent(live, self.blocks, lookups, self._declared(), self._music)
        self.started_at = time.time()
        # Why the call ended, when our code knows: it wins over livekit's close reason.
        self.ended: tuple[EndReason, EndedBy] | None = None
        self.closed_for: CloseReason | None = None
        self.closed = False
        # Set once close() wrote call.ended and sealed; the chat door closes its socket on it.
        self.over = asyncio.Event()
        self.closing: asyncio.Task[None] | None = None
        self.dead_end = False
        self.language: str | None = None
        self.usage: list[measured.ModelUsage] = []
        self.on_hold = False
        self.waiting: asyncio.Task[None] | None = None
        # Given at start; a written call has neither.
        self.room: room.CallRoom | None = None
        self.hold: HoldMusic | None = None
        # The one participant the session listens to: the caller's leg, or the talk seat.
        self.seat: str | None = None

    # ── the session's life ──

    # call.started comes first, so it is written before livekit starts and says anything.
    async def start(
        self,
        *,
        where: room.CallRoom | None = None,
        hold: HoldMusic | None = None,
        seat: str | None = None,
        opening: str | None = None,
    ) -> None:
        """Open a new call: its first entries, the session, the opening, the greeting."""
        self.room = where
        self.hold = hold
        self.seat = seat
        self.call.writing.open()
        context = self.call.context
        door = context.route.number or self.call.config.slug
        started = wire.CallStarted.model_validate(started_entry(context, door, self.started_at))
        await self.call.writing.write("call.started", started)
        knowledge = _prompt.knowledge_changed(self.blocks)
        if knowledge is not None:
            await self.call.writing.write("prompt.changed", knowledge)
        await self._opened(llm.ChatContext.empty())
        if opening is not None:
            self.live.say(opening, allow_interruptions=False)
        greeting = _prompt.greeting_for(self.call.config.greeting, context.run)
        if greeting is not None and greeting.say is not None:
            self.live.say(
                greeting.say, allow_interruptions=given_or_unset(greeting.allow_interruptions)
            )
        elif greeting is not None and greeting.reply is not None:
            interruptible = given_or_unset(greeting.allow_interruptions)
            self.live.generate_reply(instructions=greeting.reply, allow_interruptions=interruptible)

    # The caller is in the middle of the conversation: no call.started, no greeting.
    async def resume(self, history: llm.ChatContext) -> None:
        """Open a call taken up again, with the history it had."""
        self.call.writing.open()
        await self._opened(history)

    # In a worker the job's shutdown closes the session; a written call in the gateway has no
    # job, so the hang-up closes it itself.
    def hang_up(self, reason: EndReason, by: EndedBy = "agent", *, at_once: bool = False) -> None:
        """End the call for this reason: the session, and the job that runs it."""
        self.ended = (reason, by)
        self.live.shutdown(drain=not at_once)
        job = get_job_context(required=False)
        if job is not None:
            job.shutdown(reason=reason)
            return
        self.closing = asyncio.create_task(self.close())

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
            self.over.set()

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
            case SessionConfigure():
                await self.call.configure(command)
            case _:
                return False
        return True

    async def _spoken(self, command: WireModel) -> bool:
        match command:
            case AgentSay():
                self.live.say(
                    command.text, allow_interruptions=given_or_unset(command.allow_interruptions)
                )
            case AgentReply():
                self.live.generate_reply(
                    instructions=command.instructions,
                    allow_interruptions=given_or_unset(command.allow_interruptions),
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
        changed = wire.PromptChanged(name=name, hash=_prompt.hashed(text), chars=len(text))
        await self.call.writing.write("prompt.changed", changed)

    # ── the line ──

    async def hold_the_line(self) -> None:
        """The agent neither speaks nor hears, the melody plays, and the log says so."""
        if self.on_hold:
            return
        self.on_hold = True
        await silence(self.live)
        if self.hold is not None:
            self.hold.began()
        await self.call.writing.write("call.line", wire.CallLine(held=True, muted=False))

    async def give_the_line_back(self) -> None:
        """The melody stops and the agent hears and speaks again."""
        if not self.on_hold:
            return
        self._melody_ends()
        hearing_again(self.live)
        await self.call.writing.write("call.line", wire.CallLine(held=False, muted=False))

    async def stop_the_melody(self) -> None:
        """The melody stops and the line is written free; whoever took it keeps it."""
        if not self.on_hold:
            return
        self._melody_ends()
        await self.call.writing.write("call.line", wire.CallLine(held=False, muted=False))

    async def ask_for_a_person(self, wanted: CallAttention) -> None:
        """Hold the caller for a supervisor, for as long as the app asked, once at a time."""
        if self.call.waiting_for_a_person:
            raise DeclarationRefused(ALREADY_WAITING)
        self.call.waiting_for_a_person = True
        params = wire.AttentionRequested(reason=wanted.reason, wait_s=wanted.wait_s)
        await self.call.writing.write("attention.requested", params)
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

    def _melody_ends(self) -> None:
        self.on_hold = False
        if self.hold is not None:
            self.hold.ended()

    async def _take_over(self, by: Supervisor) -> None:
        if self.call.taken_by is not None:
            raise DeclarationRefused(ALREADY_HELD.format(id=self.call.taken_by.id))
        await self.call.writing.write("supervisor.took_over", wire.SupervisorTookOver(by=by))
        if self.call.waiting_for_a_person:
            await self._answered(wire.AttentionAnswered(ok=True, by=by))
        await self.stop_the_melody()
        await silence(self.live)
        self.call.taken_by = by

    async def _release(self, by: Supervisor) -> None:
        if self.call.taken_by is None:
            raise DeclarationRefused(NOBODY_HOLDS)
        await self.call.writing.write("supervisor.released", wire.SupervisorReleased(by=by))
        hearing_again(self.live)
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
        self, where: room.CallRoom, wanted: CallTransfer, *, by: Supervisor | None
    ) -> None:
        if by is not None:
            mode = await where.mode_of(wanted)
            params = wire.SupervisorTransferred(by=by, to=wanted.to, mode=mode)
            await self.call.writing.write("supervisor.transferred", params)
            wanted = CallTransfer(to=wanted.to, mode=mode)
        transferred = await where.transfer(wanted, self.live)
        await self.call.writing.write("call.transferred", transferred)
        if not transferred.ok:
            return
        self.ended = ("transferred", "agent")
        if transferred.mode == "warm":
            await silence(self.live)
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
                self.usage = [usage_of(used) for used in event.usage.model_usage]
            case ComponentFailed():
                self._failed(event)
            case CloseEvent():
                self.closed_for = event.reason
            case _:
                return

    # Interims also start the lookups, so recall and search run while the caller still talks.
    def _transcribed(self, event: UserInputTranscribedEvent) -> None:
        self.language = event.language or self.language
        text = wire.UserTranscript(
            text=event.transcript, final=event.is_final, language=event.language
        )
        self.call.writing.write("user.transcript", text)
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
        eou = end_of_utterance(item.metrics, speech)
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
        data = str(event.error)
        wrapped = getattr(event.error, "error", None)
        if not (isinstance(wrapped, APIStatusError) and forever(wrapped)):
            recoverable = bool(getattr(event.error, "recoverable", False))
            failed = wire.ErrorEvent(code="component_failed", message=data, recoverable=recoverable)
            self.call.writing.write("error", failed)
            return
        self.dead_end = True
        dead = wire.ErrorEvent(code="component_dead_end", message=data, recoverable=False)
        self.call.writing.write("error", dead)
        self.hang_up("error", "platform")

    # Deferred a tick: livekit stamps the speech id on the same object from a listener of its
    # own, and the order of listeners is not defined. Streaming ears report usage at their own
    # cadence; those ticks ride the stream and the total reaches the summary.
    def _measured(self, block: metrics.AgentMetrics) -> None:
        asyncio.get_running_loop().call_soon(self._write_block, block)

    def _write_block(self, block: metrics.AgentMetrics) -> None:
        written = block_of(block)
        if written is None:
            return
        kind, model = written
        tick = isinstance(block, metrics.STTMetrics) and block.streamed and not block.acquire_time
        self.call.writing.write(kind, model, ephemeral=True if tick else None)

    # ── what the agent is handed ──

    def _declared(self) -> list[llm.Tool | llm.Toolset]:
        config = self.call.config
        declared: list[llm.Tool | llm.Toolset] = [
            *tools.as_livekit_tools(config.tools, self._run_app_tool),
            *tools.as_livekit_tools(tools.platform_tools(config), self._run_lookup),
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
                    on_tool_completed=nothing_after,
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
        # The caller is pinned before livekit subscribes, or it links the first seat of a kind it
        # accepts, a supervisor's as soon as a caller's. A written call in a room hears no audio.
        # record is said: left unset, livekit's own recorder asks the server whether to run.
        spoken = any(isinstance(built_one, stt.STT) for built_one in self.built)
        options = RoomOptions(
            participant_identity=given_or_unset(self.seat),
            audio_input=NOT_GIVEN if spoken else False,
            audio_output=NOT_GIVEN if spoken else False,
        )
        await self.live.start(  # pyright: ignore[reportUnknownMemberType]
            self.agent, room=on, room_options=options, record=False
        )
        for component in self.built:
            component.on("metrics_collected", self._measured)  # pyright: ignore[reportUnknownMemberType]
        given = history.copy()
        given.items[:0] = list(tools.date_pair(self.call.context.today))
        await self.agent.update_chat_ctx(given, exclude_invalid_function_calls=False)

    # A note joins the history as livekit appends: copy, add, update. Never a static block,
    # which would spend the cache.
    async def _noted(self, note: str) -> None:
        options = self.agent.chat_ctx.copy()
        options.add_message(role="system", content=note)
        await self.agent.update_chat_ctx(options, exclude_invalid_function_calls=False)

    # Names the state holds (a patient's) join the declared words; livekit replaces the
    # session's keyterms in place.
    def _tell_the_ears(self, state: JsonObject) -> None:
        ears = next((built_one for built_one in self.built if isinstance(built_one, stt.STT)), None)
        if ears is not None and ears.capabilities.keyterms:
            self.live.update_options(keyterms=keyterms(self.call.config, state))

    def _how_it_ended(self) -> tuple[EndReason, EndedBy]:
        if self.ended is not None:
            return self.ended
        return A_PLATFORM_ERROR if self.closed_for is None else HOW_IT_ENDED[self.closed_for]

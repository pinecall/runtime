"""What a protocol command does to a live session: say it, answer now, rewrite the prompt, stop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from livekit.agents.types import NOT_GIVEN
from livekit.agents.voice import AgentSession

from pinecall.session.voice import transfer
from pinecall.session.voice.attending import Attending
from pinecall.session.voice.bridging import Bridged
from pinecall.session.voice.line import Line
from pinecall.session.voice.room import dtmf, invite, mute, remove, send
from pinecall.session.voice.room.holding import Holding
from pinecall_protocol import Command, ProtocolError, WireModel, command_of
from pinecall_protocol.commands import (
    AgentReply,
    AgentSay,
    CallAttention,
    CallCallback,
    CallDtmf,
    CallEvent,
    CallLog,
    CallTransfer,
    ParticipantMute,
    ParticipantRemove,
    PromptSet,
    RoomInvite,
    RoomSend,
    StateSet,
    SupervisorVerb,
    ToolsSet,
)
from pinecall_protocol.defs import EndedBy, EndReason, ToolSpec

# The type only, and never at import time: supervising.py reaches the call's ending through
# the Ending declared below, so naming its module here for real would close the circle.
if TYPE_CHECKING:
    from pinecall.session.voice.supervising import Supervising

# A command this bridge does not hold — `call.mute` and `call.unmute` are in the wire and have no
# runtime — is refused by name, in the protocol's own words, rather than accepted and dropped.
NOT_HERE = "{type} is not a verb this runtime holds yet"

# A room verb reached a call that has no room: a text session, or a test with no job behind it.
NO_ROOM = "{type} needs the room, and this call has none"

# A supervise verb reached a call whose session was never opened, so there is nothing to say it to.
NO_SESSION = "supervisor.verb needs a live session, and this call has none yet"

# A verb that acts on the line itself reached a call that has none: a test, a session not yet live.
NO_LINE = "{type} needs a live line, and this call has none"

# The agent hung up, as call.ended names who ended a call.
BY_THE_AGENT: EndReason = "agent_hung_up"


class Prompting(Protocol):
    """The prompt as the applier reaches it: its blocks by name, and which tools the model sees."""

    async def set_prompt(self, name: str, text: str) -> None:
        """One block rewritten whole, by name; an undeclared name is refused with the name."""
        ...

    async def set_tools(self, tools: Sequence[ToolSpec]) -> None:
        """The subset of the declared tools the model may see from here on."""
        ...


# The three commands whose whole effect is a line in the log. They reach the worker like every
# other one, because the process running the call is the process that writes its log — the same
# rule the text session lives by, and the reason there is one command transport and not two.
class Recording(Protocol):
    """The call's own log, as the commands that are nothing but an entry reach it."""

    async def set_state(self, state: Mapping[str, Any], changed: Sequence[str]) -> None:
        """The app's state moved, and the whole of it travels with what changed."""
        ...

    async def receives(self, name: str, data: Mapping[str, Any]) -> None:
        """A fact from the tenant's backend, refused by name when the agent declared no such one."""
        ...

    async def log_custom(self, name: str, data: Mapping[str, Any]) -> None:
        """A line of the app's own, with a seq like everything else."""
        ...

    async def call_back(self, wanted: CallCallback) -> None:
        """A number to ring back, with what the call was about: the session knows who is asking."""
        ...


class Ending(Protocol):
    """How this call ends, as the bridge does it: the two entries, then the log is sealed."""

    async def hangup(
        self, reason: EndReason, by: EndedBy = "agent", *, at_once: bool = False
    ) -> None:
        """The call ended, and call.ended says whose doing it was; at_once cuts what plays."""
        ...

    def transferred(self) -> None:
        """A cold transfer took: the caller left for another number, and did not hang up."""
        ...


type Applier = Callable[["Applying", WireModel], Awaitable[None]]


class Applying:
    """One live call and everything a command may reach: session, prompt, ending, room."""

    def __init__(
        self,
        live: AgentSession[None],
        prompting: Prompting,
        ending: Ending,
        recording: Recording,
        holding: Holding | None = None,
        supervising: Supervising | None = None,
        line: Line | None = None,
        attending: Attending | None = None,
    ) -> None:
        self.live = live
        self.prompting = prompting
        self.ending = ending
        self.recording = recording
        self.holding = holding
        self.supervising = supervising
        self.line = line
        self.attending = attending

    def held(self, verb: str) -> Holding:
        """The room this verb acts on, or a refusal by name when the call has none."""
        if self.holding is None:
            raise ProtocolError(NO_ROOM.format(type=verb))
        return self.holding

    def the_line(self, verb: str) -> Line:
        """The line this verb holds or gives back, or a refusal when the call has none."""
        if self.line is None:
            raise ProtocolError(NO_LINE.format(type=verb))
        return self.line

    def asking(self, verb: str) -> Attending:
        """The ask for a person on this call, or a refusal when the call has no line to hold."""
        if self.attending is None:
            raise ProtocolError(NO_LINE.format(type=verb))
        return self.attending

    def supervised(self) -> Supervising:
        """The desk's hand on this call, or a refusal when no session was ever opened."""
        if self.supervising is None:
            raise ProtocolError(NO_SESSION)
        return self.supervising


async def apply(applying: Applying, command: Command) -> None:
    """One command onto this call. A command the bridge does not hold is refused by name."""
    applier = APPLIERS.get(command.type)
    if applier is None:
        raise ProtocolError(NOT_HERE.format(type=command.type))
    await applier(applying, command_of(command))


# ── the verbs ───────────────────────────────────────────────────────────────────


# session.say verbatim: no model, no tools, and the sentence enters the history as the agent's
# own so the next turn knows it was said.
async def _say_it(applying: Applying, said: WireModel) -> None:
    """agent.say: the agent says this, exactly this, with nothing in the loop."""
    wanted = _as(said, AgentSay)
    interruptible = NOT_GIVEN if wanted.allow_interruptions is None else wanted.allow_interruptions
    applying.live.say(wanted.text, allow_interruptions=interruptible)


async def _reply_now(applying: Applying, said: WireModel) -> None:
    """agent.reply: one model turn now, guided by an instruction the caller never hears."""
    wanted = _as(said, AgentReply)
    interruptible = NOT_GIVEN if wanted.allow_interruptions is None else wanted.allow_interruptions
    applying.live.generate_reply(
        instructions=wanted.instructions, allow_interruptions=interruptible
    )


async def _set_the_prompt(applying: Applying, said: WireModel) -> None:
    """prompt.set: one block of the prompt rewritten whole, by name."""
    wanted = _as(said, PromptSet)
    await applying.prompting.set_prompt(wanted.name, wanted.text)


async def _set_the_tools(applying: Applying, said: WireModel) -> None:
    """tools.set: what the model may call from here on. The instructions are not touched."""
    wanted = _as(said, ToolsSet)
    await applying.prompting.set_tools(wanted.tools)


# The caller's leg leaves the room the moment a COLD transfer takes, and a caller who left is a
# caller who hung up as far as the session can tell — so a transfer that took says so itself, and
# call.ended reads `transferred` instead of `caller_hung_up`. A WARM one moves nobody: the person
# is dialled into this room, the agent falls silent, and Bridged ends the call with them.
async def _send_the_caller_on(applying: Applying, said: WireModel) -> None:
    """call.transfer: the caller sent on, or the far end dialled in. ok=False: nothing moved."""
    wanted = _as(said, CallTransfer)
    holding = applying.held(transfer.VERB)
    mode = await transfer.the_mode(holding, wanted)
    # The caller has to hear the sentence that announced this before the line moves.
    await transfer.after_the_announcement(applying.live)
    if mode == transfer.COLD:
        outcome = await transfer.sent_on(holding, wanted)
    else:
        outcome = await transfer.dialled_in(holding, wanted)
    holding.writing.later("call.transferred", outcome)
    if not outcome.ok:
        return
    if mode == transfer.COLD:
        applying.ending.transferred()
    else:
        await Bridged(applying.live, holding, applying.ending).took(
            f"{transfer.LEG_PREFIX}{wanted.to}"
        )


async def _hold_the_line(applying: Applying, said: WireModel) -> None:  # noqa: ARG001 — call.hold is empty
    """call.hold: the caller waits with the melody, and the agent neither speaks nor hears."""
    await applying.the_line("call.hold").hold()


async def _give_the_line_back(applying: Applying, said: WireModel) -> None:  # noqa: ARG001 — call.unhold is empty
    """call.unhold: the wait is over and the agent has the caller back."""
    await applying.the_line("call.unhold").unhold()


# The ask is written down and the caller goes on hold; what answers it is a supervisor's takeover,
# minutes later and through another door entirely, or nobody, and then the wait runs out.
async def _ask_for_a_person(applying: Applying, said: WireModel) -> None:
    """call.attention: the caller waits for somebody to take the line, for as long as was asked."""
    await applying.asking("call.attention").asked(_as(said, CallAttention))


async def _set_the_state(applying: Applying, said: WireModel) -> None:
    """state.set: the app's state moved, and the whole of it travels with what changed it."""
    wanted = _as(said, StateSet)
    changed = wanted.changed if wanted.changed is not None else sorted(wanted.state)
    await applying.recording.set_state(wanted.state, changed)


async def _take_an_event(applying: Applying, said: WireModel) -> None:
    """call.event: a fact from the tenant's backend, which the agent must have declared."""
    fact = _as(said, CallEvent)
    await applying.recording.receives(fact.name, fact.data)


async def _call_them_back(applying: Applying, said: WireModel) -> None:
    """call.callback: the caller asked to be rung back, and the log is where that is written."""
    await applying.recording.call_back(_as(said, CallCallback))


async def _write_a_line(applying: Applying, said: WireModel) -> None:
    """call.log: a line of the app's own in this call's log, with a seq like everything else."""
    line = _as(said, CallLog)
    await applying.recording.log_custom(line.name, line.data)


async def _hang_up(applying: Applying, said: WireModel) -> None:  # noqa: ARG001 — call.hangup is empty
    """call.hangup: the agent ends the call, and the log says who did."""
    await applying.ending.hangup(BY_THE_AGENT)


# Every supervise verb writes its own supervisor.* entry before the session acts on it, so the
# caller's log never has a gap where a human spoke. The transfer is the one verb that comes back
# here: `call.transfer` already owns the leg, the outcome entry and the ending, and one transfer is
# one answer to "did it work", however it was asked for.
async def _a_supervise_verb(applying: Applying, said: WireModel) -> None:
    """supervisor.verb: the entry with who asked, then what livekit does about it."""
    wanted = await applying.supervised().apply(_as(said, SupervisorVerb))
    if wanted is not None:
        await _send_the_caller_on(applying, wanted)


# A room verb is a function of the room and its typed command, in its own file under room/; the
# wrapper is what checks the shape and that this call has a room at all.
def _in_the_room[T: WireModel](
    verb: str, shape: type[T], act: Callable[[Holding, T], Awaitable[None]]
) -> Applier:
    """One room verb as an applier: the room it acts on, and the command as the shape it names."""

    async def applied(applying: Applying, said: WireModel) -> None:
        await act(applying.held(verb), _as(said, shape))

    return applied


APPLIERS: dict[str, Applier] = {
    "agent.say": _say_it,
    "agent.reply": _reply_now,
    "prompt.set": _set_the_prompt,
    "tools.set": _set_the_tools,
    "state.set": _set_the_state,
    "call.event": _take_an_event,
    "call.log": _write_a_line,
    "call.callback": _call_them_back,
    "call.hangup": _hang_up,
    "call.hold": _hold_the_line,
    "call.unhold": _give_the_line_back,
    "call.attention": _ask_for_a_person,
    "supervisor.verb": _a_supervise_verb,
    transfer.VERB: _send_the_caller_on,
    "room.invite": _in_the_room("room.invite", RoomInvite, invite.dialled),
    "room.send": _in_the_room("room.send", RoomSend, send.sent),
    "participant.mute": _in_the_room("participant.mute", ParticipantMute, mute.muted),
    "participant.remove": _in_the_room("participant.remove", ParticipantRemove, remove.removed),
    dtmf.VERB: _in_the_room(dtmf.VERB, CallDtmf, dtmf.sent),
}


def _as[T: WireModel](said: WireModel, shape: type[T]) -> T:
    """The command's data as the model its type names, refused when it is not that shape."""
    if not isinstance(said, shape):
        raise ProtocolError(f"a command carries {type(said).__name__}, not {shape.__name__}")
    return said

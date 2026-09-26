"""The six supervise verbs on a text call: the entry first, then what the session does about it."""

from __future__ import annotations

from livekit.agents import llm as agents

from pinecall.session.history import remembered
from pinecall.session.supervise_prompts import (
    A_RELEASE,
    A_WHISPER,
    ALREADY_HELD,
    BY_A_SUPERVISOR,
    NO_LINE_TO_TRANSFER,
    NOBODY_HOLDS,
    THE_SUPERVISOR,
)
from pinecall.session.text.session import TextSession
from pinecall_protocol import ProtocolError, verbs
from pinecall_protocol.commands import SupervisorVerb
from pinecall_protocol.defs import Supervisor
from pinecall_protocol.events import (
    SupervisorEnded,
    SupervisorReleased,
    SupervisorSaid,
    SupervisorTookOver,
    SupervisorWhispered,
)


# The worker's applier and this one are the same six verbs on two different sessions: there is no
# shared base class, because the halves that differ — a room's audio switches against a queue of
# text — are the whole body of every method. What IS shared lives in session/supervise_prompts.py.
# See docs/decisions/supervise.md.
async def applied(session: TextSession, command: SupervisorVerb) -> None:
    """One verb: its entry, then the session. A ProtocolError is a refusal the door answers with."""
    by, verb = command.by, command.verb
    match verb:
        case verbs.SayVerb():
            await _say(session, by, verb.text)
        case verbs.WhisperVerb():
            await _whisper(session, by, verb.text)
        case verbs.TakeoverVerb():
            await _take_over(session, by)
        case verbs.ReleaseVerb():
            await _release(session, by)
        case verbs.EndVerb():
            await _end(session, by, verb.reason)
        case verbs.TransferVerb():
            raise ProtocolError(NO_LINE_TO_TRANSFER)


# ── the five verbs a thread has ─────────────────────────────────────────────────


# Whether or not a human is holding the thread: while held, THIS is how the person at the desk
# writes to the contact, and the channel's own watcher delivers the turn.agent it makes.
async def _say(session: TextSession, by: Supervisor, text: str) -> None:
    """say: the agent writes the supervisor's words, word for word, to the caller."""
    await session.emit("supervisor.said", SupervisorSaid(by=by, text=text))
    await session.say(text)


async def _whisper(session: TextSession, by: Supervisor, text: str) -> None:
    """whisper: an instruction the caller never reads, binding from the next sentence on."""
    await session.emit("supervisor.whispered", SupervisorWhispered(by=by, text=text))
    note = A_WHISPER.format(text=text)
    await remembered(session.text_agent, agents.ChatMessage(role="system", content=[note]))
    # A human is on the thread: a turn generated now would write over what they are typing.
    if session.taken_by is None:
        await session.nudged(note)


async def _take_over(session: TextSession, by: Supervisor) -> None:
    """takeover: the model answers nothing more, and does not hear what it did not answer."""
    if session.taken_by is not None:
        raise ProtocolError(ALREADY_HELD.format(id=session.taken_by.id))
    await session.emit("supervisor.took_over", SupervisorTookOver(by=by))
    # A contact waiting for a person just got one: the ask is answered by the same verb.
    await session.attending.taken_by(by)
    session.taken_by = by


async def _release(session: TextSession, by: Supervisor) -> None:
    """release: the agent answers again, knowing only that it missed something."""
    if session.taken_by is None:
        raise ProtocolError(NOBODY_HOLDS)
    await session.emit("supervisor.released", SupervisorReleased(by=by))
    session.taken_by = None
    await remembered(session.text_agent, agents.ChatMessage(role="system", content=[A_RELEASE]))
    await session.nudged(A_RELEASE)


async def _end(session: TextSession, by: Supervisor, reason: str | None) -> None:
    """end: the desk closes the thread, and call.ended says a supervisor did it."""
    await session.emit("supervisor.ended", SupervisorEnded(by=by, reason=reason))
    await session.hangup(BY_A_SUPERVISOR, THE_SUPERVISOR)

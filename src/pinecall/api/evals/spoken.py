"""Ring 2: one golden said out loud on a real line, and the log it leaves for the same judges."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

from pinecall._settings import Settings
from pinecall.api.evals.conversation import Conversation
from pinecall.auth.scopes import a_visitor
from pinecall.evals.calling import Line, a_simulated_call
from pinecall.evals.goldens import Golden
from pinecall.log.entry import Entry
from pinecall.log.replay import whole
from pinecall.log.store import Store
from pinecall_protocol.defs import AgentState
from pinecall_protocol.events import AgentStateChanged
from pinecall_protocol.registry import TERMINAL_EVENT, EventType

# The worker seals the log after the caller's leg goes: `call.summary` and `call.score` are
# written by the process that held the session, not by this one, and a judge that read the log
# before they landed would score a call that had not finished. Polled, because nothing on the
# wire announces a seal to a reader that is not streaming it.
A_SEAL_MAY_TAKE_S = 25.0
_LOOKING_AGAIN_IN_S = 0.25

# How long the caller holds the line open after its last word. A golden whose expectation is a
# tool on the last turn — most of them — is judged on whether the agent got there, so the line
# cannot drop when the caller stops talking: the first spoken run of `identifica-al-paciente`
# came back "ran no tool at all" for exactly that, on a call that had not finished. Thirty and not
# twenty because a turn that runs a tool speaks twice, and the two TTS rounds around it measured
# thirteen seconds on 2026-09-11.
AN_ANSWER_MAY_TAKE_S = 30.0

# What livekit publishes about itself, and the one of its five words that means "I have finished
# and it is your turn". Both typed by the protocol, so a misspelling is a type error and not a
# run that never hangs up.
AGENT_STATE: EventType = "agent.state"
IT_IS_LISTENING: AgentState = "listening"

NOBODY_SEALED = (
    "the spoken call {call} never sealed: the worker wrote no {terminal} within {seconds:.0f}s, "
    "so there is no finished call to judge"
)


async def a_spoken_conversation(
    golden: Golden,
    *,
    call: str,
    run: str,
    model: str,
    agent: str,
    store: Store,
    settings: Settings,
    line: Line | None = None,
    app: str | None = None,
) -> Conversation:
    """Dispatch the agent into a room, say the golden's lines out loud, and read the log back."""
    said = _the_lines_of(golden)
    spoken = _Reading(said)
    await a_simulated_call(
        call,
        agent,
        turns=len(said),
        next_line=spoken.next_line,
        line=line or Line(interferer_db=None, packet_loss=0.0),
        settings=settings,
        caller=a_visitor(),
        run=run,
        app=app,
        settled=lambda so_far: _until_the_answer_lands(store, call, so_far),
    )
    entries = await _once_it_is_sealed(store, call)
    return Conversation(golden=golden, model=model, call=call, entries=entries)


def _the_lines_of(golden: Golden) -> tuple[str, ...]:
    """What the caller says, in order: the golden's own input, said and not improvised."""
    return tuple(line for line in golden.input if line)


class _Reading:
    """The golden's lines handed out one per turn. It holds a cursor and decides nothing else."""

    def __init__(self, lines: Sequence[str]) -> None:
        self._lines = lines
        self._said = 0

    async def next_line(self, _turns_left: int) -> tuple[str, bool]:
        """The next line. Never a hangup: the run holds the line until the answer has landed."""
        if self._said >= len(self._lines):
            return "", False
        line = self._lines[self._said]
        self._said += 1
        return line, False


# Counted rather than timed: the agent has answered when there is one of its turns for each of
# the caller's, which is the same thing ring 1's settling waits for and the only signal that does
# not guess. A turn that never comes is the deadline, and the judges see the call as it really was.
async def _until_the_answer_lands(store: Store, call: str, said: int) -> None:
    """Hold the line until the agent has answered the last line, or until it plainly will not."""
    deadline = time.monotonic() + AN_ANSWER_MAY_TAKE_S
    while time.monotonic() < deadline:
        if the_answer_has_landed(await whole(store, call), said):
            return
        await asyncio.sleep(_LOOKING_AGAIN_IN_S)


# Why neither a count of turns nor a tool round is the signal. `en-el-chat-ofrece-mas-de-dos-horas`,
# spoken, 2026-09-11: the agent called freeSlots, then SAID "voy a consultar qué hay libre el
# lunes" — a filler, spoken after the tool had already answered, because the model writes its
# preamble and its tool call in one response and livekit speaks that preamble once the round is
# done. That filler is a `turn.agent` landing after the tool, so a run watching for either one hung
# up on it: 234 milliseconds later, in the middle of the generation that had the hours in it. On
# the call before it the real answer arrived one second after the caller had already gone.
#
# The signal is the one livekit publishes about itself and the log already carries: the agent goes
# `thinking`, `speaking`, and back to `listening` when it has nothing left to say. A filler leaves
# it thinking. So the line is held until it is listening again, and that is neither a guess nor a
# count of anything.
def the_answer_has_landed(entries: Sequence[Entry], said: int) -> bool:
    """Every line heard, and the agent back to listening after the last of them."""
    heard = [at for at, entry in enumerate(entries) if entry.type == "turn.user"]
    if len(heard) < said:
        return False
    states = [(at, entry) for at, entry in enumerate(entries) if entry.type == AGENT_STATE]
    if not states:
        return False
    at, last = states[-1]
    return at > heard[-1] and AgentStateChanged.model_validate(last.data).state == IT_IS_LISTENING


async def _once_it_is_sealed(store: Store, call: str) -> Sequence[Entry]:
    """The whole log, once the worker has closed it. Refused rather than judged half-written."""
    deadline = time.monotonic() + A_SEAL_MAY_TAKE_S
    while True:
        entries = await whole(store, call)
        if any(entry.type == TERMINAL_EVENT for entry in entries):
            return entries
        if time.monotonic() >= deadline:
            raise TimeoutError(
                NOBODY_SEALED.format(call=call, terminal=TERMINAL_EVENT, seconds=A_SEAL_MAY_TAKE_S)
            )
        await asyncio.sleep(_LOOKING_AGAIN_IN_S)

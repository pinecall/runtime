"""Ring 2: one golden said out loud on a real line, and the log it leaves for the same judges."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from uuid import uuid4

from pinecall._settings import Settings
from pinecall.api.evals.conversation import A_CALLER, Conversation
from pinecall.evals.calling import Line, a_simulated_call
from pinecall.evals.goldens import Golden
from pinecall.log.entry import Entry
from pinecall.log.replay import whole
from pinecall.log.store import Store
from pinecall_protocol.registry import TERMINAL_EVENT

# A spoken golden says the lines the tenant wrote and nothing else: no model plays the caller
# here, which is the whole difference from `simulate`. What ring 2 measures is the agent against
# real ears and a real voice, not a conversation two models improvised at each other.
THE_LINES_ARE_THE_TENANTS = True

# The worker seals the log after the caller's leg goes: `call.summary` and `call.score` are
# written by the process that held the session, not by this one, and a judge that read the log
# before they landed would score a call that had not finished. Polled, because nothing on the
# wire announces a seal to a reader that is not streaming it.
A_SEAL_MAY_TAKE_S = 25.0
_LOOKING_AGAIN_IN_S = 0.25

# How long the caller holds the line open after its last word. A golden whose expectation is a
# tool on the last turn — most of them — is judged on whether the agent got there, so the line
# cannot drop when the caller stops talking: the first spoken run of `identifica-al-paciente`
# came back "ran no tool at all" for exactly that, on a call that had not finished.
AN_ANSWER_MAY_TAKE_S = 20.0

# The two entries a tool round leaves. The agent's last word has to come after both of them.
_A_TOOL_ROUND = frozenset({"tool.call", "tool.result"})

NOBODY_SEALED = (
    "the spoken call {call} never sealed: the worker wrote no {terminal} within {seconds:.0f}s, "
    "so there is no finished call to judge"
)


async def a_spoken_conversation(
    golden: Golden,
    *,
    call: str,
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
        caller=_a_spoken_caller(),
        app=app,
        settled=lambda: _until_the_answer_lands(store, call, len(said)),
    )
    entries = await _once_it_is_sealed(store, call)
    return Conversation(golden=golden, model=model, call=call, entries=entries)


# The same prefix a written eval call is minted with, because it is the same marker the app reads
# to know which golden's state this call opens in (cli/testing/seeding.ts). A spoken call that did
# not carry it would reach the class with an empty state and every seeded golden would be a lie.
def _a_spoken_caller() -> str:
    """Who the app is told is calling: an eval caller, so the seeding seam fires as in ring 1."""
    return f"{A_CALLER}{uuid4().hex[:12]}"


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
        if _the_answer_has_landed(await whole(store, call), said):
            return
        await asyncio.sleep(_LOOKING_AGAIN_IN_S)


# Why the count alone is not enough. `en-el-chat-ofrece-mas-de-dos-horas`, spoken, 2026-09-11: the
# agent called freeSlots and said "voy a consultar qué hay libre el lunes". That is one turn for
# one line, the count was satisfied, the caller hung up — and the hours it had gone to fetch were
# never read out. The judge then reported that the agent never said "nueve", which was true and
# was the harness's doing. A turn that ANNOUNCES a tool is not the answer to anything.
def _the_answer_has_landed(entries: Sequence[Entry], said: int) -> bool:
    """Every line heard, and the agent's last word after both the last of them and any tool."""
    answers = [at for at, entry in enumerate(entries) if entry.type == "turn.agent"]
    heard = [at for at, entry in enumerate(entries) if entry.type == "turn.user"]
    if not answers or len(heard) < said or answers[-1] < heard[-1]:
        return False
    tools = [at for at, entry in enumerate(entries) if entry.type in _A_TOOL_ROUND]
    return not tools or answers[-1] > tools[-1]


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

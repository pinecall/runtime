"""Ring 2: one golden said out loud on a real line, and the log it leaves for the same judges."""

from __future__ import annotations

from collections.abc import Sequence

from pinecall.api.evals.agent_finished import until_the_answer_lands
from pinecall.api.evals.golden_call import Conversation
from pinecall.evals.caller_voice import Speaking
from pinecall.evals.goldens import Golden
from pinecall.evals.voice_run import Line, run_simulated_call
from pinecall.evals.wait_until import until
from pinecall.log.entry import Entry
from pinecall.log.replay import whole
from pinecall.log.store import Store
from pinecall.settings import Settings
from pinecall.tokens.scopes import new_visitor_identity
from pinecall.types import Env
from pinecall_protocol.registry import TERMINAL_EVENT

# The worker seals the log after the caller's leg goes: `call.summary` and `call.score` are
# written by the process that held the session, not by this one, and a judge that read the log
# before they landed would score a call that had not finished. Polled, because nothing on the
# wire announces a seal to a reader that is not streaming it.
A_SEAL_MAY_TAKE_S = 25.0

NOBODY_SEALED = (
    "the spoken call {call} never sealed: the worker wrote no {terminal} within {seconds:.0f}s, "
    "so there is no finished call to judge"
)


async def run_spoken_conversation(
    golden: Golden,
    *,
    call: str,
    run: str,
    model: str,
    agent: str,
    store: Store,
    settings: Settings,
    org: str,
    env: Env,
    holder: str | None = None,
    line: Line | None = None,
    app: str | None = None,
    speaking: Speaking | None = None,
) -> Conversation:
    """Dispatch the agent into a room, say the golden's lines out loud, and read the log back."""
    said = _the_lines_of(golden)
    spoken = _Reading(said)
    await run_simulated_call(
        call,
        agent,
        turns=len(said),
        next_line=spoken.next_line,
        line=line or Line(interferer_db=None, packet_loss=0.0),
        settings=settings,
        org=org,
        env=env,
        holder=holder,
        caller=new_visitor_identity(),
        run=run,
        app=app,
        speaking=speaking,
        settled=lambda so_far: until_the_answer_lands(store, call, so_far),
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


async def _once_it_is_sealed(store: Store, call: str) -> Sequence[Entry]:
    """The whole log, once the worker has closed it. Refused rather than judged half-written."""
    entries: Sequence[Entry] = ()

    async def sealed() -> bool:
        nonlocal entries
        entries = await whole(store, call)
        return any(entry.type == TERMINAL_EVENT for entry in entries)

    if not await until(sealed, within_s=A_SEAL_MAY_TAKE_S):
        raise TimeoutError(
            NOBODY_SEALED.format(call=call, terminal=TERMINAL_EVENT, seconds=A_SEAL_MAY_TAKE_S)
        )
    return entries

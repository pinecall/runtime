"""A persona on a written line: each line its model improvises said to the agent, until done."""

from pinecall.domain.errors import PinecallError
from pinecall.evals.goldens import BROKE, HUNG_UP
from pinecall.evals.turns import NextLine, answered, speak_turns
from pinecall.log.logs import Logs
from pinecall.session import text
from pinecall.session.session import Session


# The written caller takes its turns as the spoken one does — the first line after the agent's
# opening, each next once the agent's whole answer is in, read off the same log — and hangs up
# last, so the call is sealed and judged like any other.
async def converse(session: Session, logs: Logs, next_line: NextLine, turns: int) -> int:
    """Start the call, say each line the caller improvises as the agent answers; the lines said."""
    call = session.call.context.call
    await session.start()
    # When the caller began its last line: the agent hears a written one the moment it is said.
    said_at = logs.store.clock()

    async def say(line: str) -> None:
        nonlocal said_at
        said_at = logs.store.clock()
        if not session.closed:
            await text.hears(session, line)

    async def wait(lines: int) -> None:
        if not session.closed:
            await answered(logs, call, lines, since=said_at)

    try:
        lines = await speak_turns(turns, next_line, say, wait)
    except PinecallError:
        await text.end(session, *BROKE)
        raise
    if not session.closed:
        await text.end(session, *HUNG_UP)
    return lines

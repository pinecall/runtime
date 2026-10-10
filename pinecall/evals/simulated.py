"""A persona on a written line: each line its model improvises said to the agent, until done."""

import asyncio

from pinecall.domain.errors import PinecallError
from pinecall.evals.goldens import BROKE, HUNG_UP, settled
from pinecall.evals.spoken import NextLine
from pinecall.log.logs import Subscription
from pinecall.session import text
from pinecall.session.session import Session
from pinecall.wire.parts import EndedBy, EndReason

# What a reply may take: a model's answer, and the tools it runs on the way.
A_REPLY_MAY_TAKE_S = 60.0

# What ends the wait for a reply: the agent's turn, or the call over under the caller.
ANSWERED = frozenset({"turn.agent", "call.ended"})

NEVER_ANSWERED: tuple[EndReason, EndedBy] = ("timeout", "platform")


# The call opens on the agent's greeting, which is heard before the caller's first line. Each line
# waits for the agent's whole answer before the caller is asked for the next, as a person waits; a
# line the model leaves empty is a caller who hung up, and a call the agent or a person ended asks
# for no more. The caller hangs up last, so the call is sealed and judged like any other.
async def converse(session: Session, heard: Subscription, next_line: NextLine, turns: int) -> int:
    """Start the call, say each line the caller improvises, then hang up; the lines said."""
    await session.start()
    lines = 0
    try:
        await settled(heard)
        for turns_left in range(turns, 0, -1):
            line, hangs_up = await next_line(turns_left)
            if not line or session.closed:
                break
            await text.hears(session, line)
            lines += 1
            if not await answered(heard):
                await text.end(session, *NEVER_ANSWERED)
                return lines
            if hangs_up:
                break
    except PinecallError:
        await text.end(session, *BROKE)
        raise
    if not session.closed:
        await text.end(session, *HUNG_UP)
    return lines


async def answered(heard: Subscription, *, within_s: float = A_REPLY_MAY_TAKE_S) -> bool:
    """Wait for the agent's turn, then for the log to settle; False when none came in time."""
    try:
        async with asyncio.timeout(within_s):
            async for entry in heard:
                if entry.type in ANSWERED:
                    break
    except TimeoutError:
        return False
    await settled(heard)
    return True

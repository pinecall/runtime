"""A call's time: its ceiling, warned before it ends, and a chat in a room ended when quiet."""

import asyncio
import time

from pinecall.session.call import CLOSING, WARNED_BEFORE_S
from pinecall.session.session import Session
from pinecall.wire import events as wire


# The limit holds while a supervisor has the line; only the warning is skipped, since a
# generated turn would talk over them.
async def keep_time(
    session: Session, limit_s: int, *, exhausted: wire.CreditsExhausted | None
) -> None:
    """Warn the agent before the limit and end the call at it; nothing when there is none."""
    if limit_s == 0:
        return
    warned_at = limit_s - WARNED_BEFORE_S if limit_s >= 2 * WARNED_BEFORE_S else limit_s / 2
    await asyncio.sleep(warned_at)
    if not session.call.a_person_has_the_line:
        session.live.generate_reply(instructions=CLOSING)
    await asyncio.sleep(limit_s - warned_at)
    if exhausted is not None:
        await session.call.writing.write("credits.exhausted", exhausted)
    session.hang_up("timeout", "platform")


# A chat in a room has no ceiling, and a visitor who leaves the page open would hold its seat for
# ever (one did for over an hour on 2026-10-03): it ends once the person has said nothing for long.
async def end_when_quiet(session: Session, quiet_s: float) -> None:
    """End the call once the person has said nothing for `quiet_s`; nothing when zero."""
    if quiet_s == 0:
        return
    while True:
        left = quiet_s - (time.monotonic() - session.last_heard)
        if left <= 0:
            session.hang_up("timeout", "platform")
            return
        await asyncio.sleep(left)

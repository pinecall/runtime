"""A bound call's pump: what any gateway writes to it, sent down its app socket in seq order."""

import logging
from collections.abc import Awaitable, Callable, Sequence

from pydantic import BaseModel

from pinecall.log.logs import Log
from pinecall.process.signal import Signal
from pinecall.wire.frames import Entry

type Send = Callable[[Entry], Awaitable[None]]

logger = logging.getLogger(__name__)

# What a stream says of itself, never sent to an app.
MARKERS = frozenset({"log.caught_up", "log.gap"})

# Where a gateway tells the one holding an app socket that a call is bound to it.
BOUND_CHANNEL = "app:{app}"


class Bound(BaseModel):
    """A call bound to an app socket another gateway holds, and the seq its pump starts after."""

    call: str
    after: int


# The app runs the call's tools and holds its state: it is sent them as they were written, from
# `after`; `then` goes down right after the first entry (the tools still waiting, after
# call.attached). The stream follows the log through the relay, so an entry another gateway
# wrote reaches the socket here, and it ends when the call does.
async def pumped(log: Log, after: int, send: Send, then: Sequence[Entry]) -> None:
    """Send the call's entries to its socket until the call ends or the socket stops taking them."""
    waiting = list(then)
    try:
        async for entry in log.stream(after=after):
            if entry.type in MARKERS:
                continue
            await send(log.opened(entry))
            for tool_call in waiting:
                await send(log.opened(tool_call))
            waiting = []
    except (OSError, RuntimeError):
        logger.warning("an app socket stopped taking its call's entries", exc_info=True)


# Lossy, as the signal is: a call whose notice was lost is told again when its worker asks a tool
# (the round trip waits on the log either way), and parked calls are handed on at every pass.
def told_bound(signal: Signal, app: str, bound: Bound) -> None:
    """Tell the gateway holding the socket that the call is bound to it, from this seq."""
    signal.publish(BOUND_CHANNEL.format(app=app), bound.model_dump_json().encode())

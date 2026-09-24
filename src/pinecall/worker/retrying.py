"""A request asked again while the gateway is away: unreachable or 5xx, on a capped backoff."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterator

from pinecall.worker.hop import GatewayRefused

logger = logging.getLogger(__name__)

# A gateway that is restarting is back in seconds: the first retries are close together, and none
# is further apart than the cap, so the call's log catches up within a few seconds of its return.
FIRST_S = 0.5
CAP_S = 5.0

# Every tenth attempt is said once in the process log: a gateway away that long is worth a line.
SAID_EVERY = 10


def delays(first_s: float = FIRST_S, cap_s: float = CAP_S) -> Iterator[float]:
    """The waits between two attempts: doubling from the first, never past the cap."""
    wait = first_s
    while True:
        yield wait
        wait = min(wait * 2, cap_s)


def away(refused: GatewayRefused) -> bool:
    """Whether the gateway is away rather than saying no: unreachable, or a 5xx while it starts."""
    return refused.status is None or refused.status >= 500


# A 4xx is the gateway's answer and is never asked again: the request itself is wrong, or the call
# is over. Only its absence is waited out — `within_s` bounds the wait for a request whose caller
# has a deadline of its own (a tool), and None waits for as long as the call lasts (an entry).
async def again[T](attempt: Callable[[], Awaitable[T]], *, within_s: float | None, what: str) -> T:
    """The attempt, asked again while the gateway is away, until it answers or the time is up."""
    started = time.monotonic()
    waits = delays()
    tries = 0
    while True:
        tries += 1
        try:
            return await attempt()
        except GatewayRefused as refused:
            wait = next(waits)
            late = within_s is not None and time.monotonic() - started + wait > within_s
            if not away(refused) or late:
                raise
            if tries % SAID_EVERY == 0:
                logger.warning(
                    "%s: the gateway is still away after %d tries (%s)", what, tries, refused
                )
            await asyncio.sleep(wait)

"""Waiting for a fact on the line: ask again until it is so, or until the deadline passes."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

# How often a waiter looks again. Fine enough that a run never idles a noticeable time after the
# fact it waits for has happened, coarse enough that a store is not asked in a tight loop.
A_LOOK_EVERY_S = 0.25


async def until(it_is_so: Callable[[], Awaitable[bool]], *, within_s: float) -> bool:
    """True the moment the fact holds; False when the deadline passed first."""
    deadline = time.monotonic() + within_s
    while True:
        if await it_is_so():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(A_LOOK_EVERY_S)

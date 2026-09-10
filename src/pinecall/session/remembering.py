"""What a session asks of the platform at hang-up: what this call taught about the contact."""

from __future__ import annotations

import asyncio
from typing import Protocol

from pinecall_protocol.events import ErrorEvent

REMEMBER_FAILED = "remember_failed"
NOT_REMEMBERED = "memory was not written: {why}"


class Rememberer(Protocol):
    """Who writes what a call taught about the contact, once, at hang-up."""

    async def remember(self, call: str) -> int:
        """Read the call's turns off its log and write the memory ops; how many were written."""
        ...


class NoRememberer:
    """A process that keeps no memory: a hang-up writes nothing."""

    async def remember(self, call: str) -> int:  # noqa: ARG002 — the protocol's shape
        """Nothing, and no op written."""
        return 0


# Between call.ended and call.summary, so every turn is in the log when memory reads it back, and
# inside a budget, so a slow model at hang-up never holds the seal: the call seals either way.
async def remembered_within(
    rememberer: Rememberer, call: str, budget_s: float
) -> ErrorEvent | None:
    """remember(call) inside its budget; what went wrong as the entry to write, or None."""
    try:
        await asyncio.wait_for(rememberer.remember(call), budget_s)
    except TimeoutError:
        return _failed_to_remember(f"no answer within {budget_s:g} s")
    except Exception as failed:  # noqa: BLE001 — the call seals whatever memory did
        return _failed_to_remember(str(failed) or type(failed).__name__)
    return None


def _failed_to_remember(why: str) -> ErrorEvent:
    return ErrorEvent(
        code=REMEMBER_FAILED, message=NOT_REMEMBERED.format(why=why), recoverable=True
    )

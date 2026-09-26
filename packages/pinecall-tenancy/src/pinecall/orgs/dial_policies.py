"""What each org may dial, kept per org, and the ledger of every dial it asked for."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pinecall.db import Pool
from pinecall.types import DialPolicy


@dataclass(frozen=True)
class Dial:
    """One dial this box was asked for: where it was aimed, who asked, and what became of it."""

    org: str
    env: str
    agent: str
    dialled: str
    asked_by: str
    call: str | None = None
    shown: str | None = None
    # The guard that said no, in one word, or None for a dial that was placed.
    refused: str | None = None


class DialPolicies(Protocol):
    """Where an org's outbound guards are kept. An org nobody set has the code's own defaults."""

    async def of(self, org: str) -> DialPolicy:
        """What this org may dial. The defaults, for an org nobody has set one for."""
        ...

    async def put(self, org: str, policy: DialPolicy) -> None:
        """Replace the org's guards, whole: a field left out is the default and not 'no limit'."""
        ...


class Dials(Protocol):
    """Every dial asked for, taken or refused: what the rate guard counts and an operator reads."""

    async def asked(self, dial: Dial) -> None:
        """Write one down. A refused dial is written too — a burst of them is the attack."""
        ...

    async def since(self, org: str, seconds: float) -> int:
        """How many this org has asked for in the last so-many seconds, refusals included."""
        ...


def dialling_for(pool: Pool | None) -> tuple[DialPolicies, Dials]:
    """The tables when this process opened a pool, and this process's own memory when it did not."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.dial_policies_memory import MemoryDialling
    from pinecall.orgs.dial_policies_postgres import PostgresDialling

    if pool is None:
        both = MemoryDialling()
        return both, both
    postgres = PostgresDialling(pool)
    return postgres, postgres


__all__ = ["Dial", "DialPolicies", "Dials", "dialling_for"]

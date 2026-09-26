"""Where an org's synthetic callers are kept: one row a caller, one list an org, whichever agent."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from pinecall.db import Pool
from pinecall.errors import PinecallError

# A caller nobody wrote, and a name taken by somebody else: the two things a write can meet.
NOBODY = "no persona called {name} in this org"
TAKEN = "this org has a persona called {name} already"


class NoSuchPersona(PinecallError):
    """The caller a read, a rename or a drop named is not one this org has."""


class NameTaken(PinecallError):
    """A rename onto a name another caller of this org already holds."""


class Personas(Protocol):
    """Every caller an org wrote, read and written by name — whichever agent ends up answering."""

    async def of(self, org: str) -> list[dict[str, Any]]:
        """Every caller of this org, by name. Each one whole: the gateway keeps no half."""
        ...

    async def named(self, org: str, name: str) -> dict[str, Any] | None:
        """One caller, or None when nobody wrote that name."""
        ...

    async def put(
        self,
        org: str,
        name: str,
        *,
        about: str,
        goal: str,
        style: str,
        facts: Mapping[str, str],
        state: Mapping[str, Any],
        author: str,
        was: str | None = None,
        llm: str | None = None,
        tts: str | None = None,
        voice: str | None = None,
        accepts_when: str = "",
        declines_when: str = "",
    ) -> list[dict[str, Any]]:
        """The caller written whole — new, replaced, or renamed from `was` — and the list after.

        A rename onto a name nobody wrote is NoSuchPersona, onto a name another caller holds is
        NameTaken, and either is refused before anything is written. Nothing is merged: what the
        page sent IS the caller. The three knobs are None when unset — the runtime's choice.
        """
        ...

    async def drop(self, org: str, name: str) -> list[dict[str, Any]]:
        """The caller gone, and the list after. A name nobody wrote is NoSuchPersona."""
        ...


def knob_of(value: Any) -> str | None:
    """One of the three knobs as the wire says it: the word that was set, or None for unset."""
    return None if value is None or value == "" else str(value)


def personas_for(pool: Pool | None) -> Personas:
    """The table when there is a database, and this process's own memory when there is none."""
    # Imported here: both adapters import this module for the port, and the one place that
    # picks between them is the one place the cycle would close (auth/members.py).
    from pinecall.orgs.personas_memory import MemoryPersonas
    from pinecall.orgs.personas_postgres import PostgresPersonas

    return MemoryPersonas() if pool is None else PostgresPersonas(pool)

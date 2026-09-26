"""Rows kept a version at a time per corner, in memory or Postgres: a tuning and a lexicon alike."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Protocol

from pinecall.errors import PinecallError
from pinecall.types import Kept

# What a write says when the corner is not at the version the writer read. Two people saving the
# same agent from two screens: the second is told, never quietly written over the first.
MOVED = "this corner is at v{newest} now, not the version you read: read it again, then set again"


class VersionMoved(PinecallError):
    """The corner's newest is not the one the writer saw, or another writer got there first."""

    def __init__(self, newest: int) -> None:
        super().__init__(MOVED.format(newest=newest))
        self.newest = newest


@dataclass(frozen=True)
class Corner:
    """Whose rows: the org, the world, the holder as the column spells it, and the agent when the
    rows are one agent's (a tuning) and not the org's (a lexicon)."""

    org: str
    env: str
    holder: str
    agent: str | None = None

    @property
    def args(self) -> tuple[str, ...]:
        """The corner as a statement takes it: $1 org, $2 env, $3 holder, then the agent."""
        return (self.org, self.env, self.holder) + (() if self.agent is None else (self.agent,))

    def held_by(self, holder: str) -> Corner:
        """The same rows in another holder's corner."""
        return dataclasses.replace(self, holder=holder)


class Versions[T](Protocol):
    """One table of versions: the newest of a corner, one version, the history, and a write."""

    async def own(self, corner: Corner) -> Kept[T] | None:
        """This corner's newest and nothing else's; None when it set nothing."""
        ...

    async def chain(self, corner: Corner) -> list[Kept[T]]:
        """The newest of each corner this one reads through, nearest first: its own, the org's."""
        ...

    async def at(self, corner: Corner, version: int) -> Kept[T] | None:
        """One version, the corner's own if it has it, else the org's own."""
        ...

    async def history(self, corner: Corner, limit: int) -> list[Kept[T]]:
        """This corner's versions, newest first."""
        ...

    async def every_chain(self, org: str, env: str, holder: str) -> dict[str, list[Kept[T]]]:
        """Every agent's chain in this world, by slug, each as this corner reads it."""
        ...

    async def put(
        self, corner: Corner, value: T, *, author: str, note: str | None, if_version: int | None
    ) -> int:
        """A new version in this corner, numbered after its last; VersionMoved when it moved."""
        ...


@dataclass(frozen=True)
class Statements:
    """One table's five statements, each taking the corner's args first (Corner.args), then
    `at`'s version, `history`'s limit, `put`'s columns, author, note and if_version."""

    own: str
    chain: str
    at: str
    history: str
    put: str
    # Every agent's chain in the world: $1 org, $2 env, $3 holder. None for a table with no agent.
    every_chain: str | None = None

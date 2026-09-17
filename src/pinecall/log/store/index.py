"""The call index: the questions a console asks across calls, answered off one row per call."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pinecall.log.facts import CallFacts

# A day, in the only clock the head rows have: unix seconds. The day is cut in UTC because an org
# carries no timezone; the door says so in its answer.
A_DAY_S = 24 * 60 * 60


@dataclass(frozen=True)
class Wanted:
    """Which calls a list asks for: one agent's, one channel's, some words, below a cursor."""

    agent: str | None = None
    channel: str | None = None
    # Matched case-insensitively against the call id's start, the digits of either number, the
    # caller's name and the outcome.
    q: str | None = None
    # The last call of the page before: the next page starts below it.
    before: str | None = None


@dataclass(frozen=True)
class Found:
    """One page of the calls that match, newest first, how many match in all, and the cursor."""

    calls: list[str]
    total: int
    next: str | None


@dataclass(frozen=True)
class AgentDay:
    """One agent's day: the calls it started, and the share of judges that held, on average."""

    slug: str
    calls: int
    score: float | None


@dataclass(frozen=True)
class Day:
    """One corner's day, counted: every number the insights door says, and the two it adds."""

    calls: int = 0
    yesterday: int = 0
    finished: int = 0
    unescalated: int = 0
    median_e2e: float | None = None
    spent: float = 0.0
    channels: dict[str, int] = field(default_factory=dict[str, int])
    agents: list[AgentDay] = field(default_factory=list[AgentDay])
    total: int = 0
    live: int = 0


@dataclass(frozen=True)
class ThreadRow:
    """One contact of an agent's inbox: its newest call's facts, and what the reader missed."""

    contact: str
    newest: CallFacts
    # When the newest thing on the thread happened: its last turn, or when the call started.
    moved_at: float
    unread: int
    calls: int
    name: str | None


@dataclass(frozen=True)
class Threads:
    """One page of an inbox and the cursor to the next one."""

    rows: list[ThreadRow]
    next: str | None


# Everything here reads what `append` already folded: no verb opens a log. Every question is asked
# of ONE corner — an org, a world and whose ("" for the org's own) — as the session list is, except
# what the org spends, which is the org's across both worlds because a budget is.
class CallIndex(Protocol):
    """The questions across an org's calls that a list, a day and an inbox ask."""

    async def facts_of(self, calls: Sequence[str]) -> dict[str, CallFacts]:
        """The facts of each of these calls that has any."""
        ...

    async def found(self, org: str, env: str, holder: str, wanted: Wanted, limit: int) -> Found:
        """The newest calls of the corner that match, a page, the count of all, and the cursor."""
        ...

    async def a_day(self, org: str, env: str, holder: str, start: float) -> Day:
        """The corner's calls that started in the day that opens at `start`, counted."""
        ...

    async def spent_since(self, org: str, since: float) -> float:
        """What every call of the org that started since then cost, every world and corner."""
        ...

    async def threads(
        self,
        org: str,
        env: str,
        holder: str,
        agent: str,
        reader: str,
        after: str | None,
        limit: int,
    ) -> Threads:
        """The agent's contacts, the one whose thread moved last first, and the reader's unread."""
        ...

    async def calls_with(
        self, org: str, env: str, holder: str, agent: str, contact: str, limit: int
    ) -> list[str]:
        """This contact's newest calls with the agent, newest first."""
        ...

    async def read(
        self, org: str, env: str, holder: str, agent: str, reader: str, contact: str, at: float
    ) -> None:
        """The reader has read the contact's thread up to this moment."""
        ...


def thread_cursor(row: ThreadRow) -> str:
    """Where the page after this row starts: when it moved, then who, since two may move at once."""
    return f"{row.moved_at!r}:{row.contact}"


def after_the_cursor(cursor: str | None) -> tuple[float, str] | None:
    """The moment and the contact a cursor names, or None for a first page or a word that is not."""
    if not cursor or ":" not in cursor:
        return None
    moved, contact = cursor.split(":", 1)
    try:
        return float(moved), contact
    except ValueError:
        return None


# A LIKE pattern is SQL's own little language: a `%` or `_` a person typed is a character they
# are looking for, never a wildcard.
def like_escaped(words: str) -> str:
    """The words with LIKE's two wildcards and its escape character escaped."""
    return words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def digits_of(words: str) -> str:
    """Only the digits: `+34 600-12` is `3460012`, which is how a number is matched."""
    return "".join(ch for ch in words if ch.isdigit())

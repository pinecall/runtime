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
class CallCorner:
    """Where one call lives: its org, its world, whose corner ("" for the org's own), its agent."""

    org: str | None
    env: str
    holder: str
    agent: str
    # Which tuning and which lexicon the call was built on (0037); None for a corner that had set
    # nothing, and for every call from before the columns.
    config_version: int | None = None
    lexicon_version: int | None = None
    # Whether the head row is sealed, and when the call's first entry landed: what a gateway that
    # forgot a live call reads to know it is still live, and which day it opened on.
    sealed: bool = False
    started_at: float | None = None

    def is_in(self, org: str, env: str, holder: str) -> bool:
        """Whether this call is in that corner."""
        return (self.org, self.env, self.holder) == (org, env, holder)


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


# A spoken call whose log never ended. Every other question here is asked of one corner; this one
# is asked of the whole store, because the reaper is the process's and not a reader's.
@dataclass(frozen=True)
class Unsealed:
    """One call still open: whose agent, when it opened, and when it last said anything."""

    call: str
    agent: str
    started_at: float
    last_at: float


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


# What a persona's pane reads: the call's own facts, and the clock the list is ordered by — the
# head row's started_at, which is not a fact (0025) and so cannot come off CallFacts.
@dataclass(frozen=True)
class PersonaRun:
    """One simulation a synthetic caller ran: when the call opened, and what the call said."""

    started_at: float
    facts: CallFacts

    @property
    def turns(self) -> int:
        """How many turns the caller took: one `heard_at` per line the model playing them said."""
        return len(self.facts.heard_at)


@dataclass(frozen=True)
class PersonaRuns:
    """One page of a caller's runs, how many there are in all, and the cursor to the next page."""

    runs: list[PersonaRun]
    total: int
    next: str | None


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

    # A call's corner is its head row's (0024), and a door that acts on ONE call by its id — a
    # judge, a message — asks it here before it acts: another org's call and another world's are
    # the same refusal.
    async def corner_of_call(self, call: str) -> CallCorner | None:
        """The org, the world and whose corner this call was opened in; None for no such call."""
        ...

    async def facts_of(self, calls: Sequence[str]) -> dict[str, CallFacts]:
        """The facts of each of these calls that has any."""
        ...

    # Every org's, because nothing is being read for anybody: this is the gateway asking its own
    # store which calls it never finished writing. Spoken only — a written one idles out in the
    # process that runs it (api/whatsapp/threads.py) — and quiet only, which is what makes the
    # answer short whatever the store holds.
    async def unsealed_spoken(self, quiet_since: float, limit: int) -> list[Unsealed]:
        """Every spoken or never-started call, its log unsealed and quiet since `quiet_since`."""
        ...

    async def found(self, org: str, env: str, holder: str, wanted: Wanted, limit: int) -> Found:
        """The newest calls of the corner that match, a page, the count of all, and the cursor."""
        ...

    # The persona is the ORG's, but its runs are calls, and a call is one corner's like every
    # other: the same cut, the same `before` cursor and the same count the session list answers.
    async def runs_of_persona(
        self, org: str, env: str, holder: str, persona: str, before: str | None, limit: int
    ) -> PersonaRuns:
        """This caller's newest simulations in the corner, a page, the count, and the cursor."""
        ...

    async def a_day(self, org: str, env: str, holder: str, start: float) -> Day:
        """The corner's calls that started in the day that opens at `start`, counted."""
        ...

    async def spent_between(self, org: str, start: float, end: float) -> float:
        """What every call of the org that started in [start, end) cost, every world and corner."""
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

    # The org's and not one corner's, the way what an org spends is: "have we ever spoken to this
    # number" is the question the dial guard asks before calling somebody back, and a caller who
    # reached a developer's sandbox copy last week still reached this org. The number is matched
    # as the fold wrote it — E.164 with its plus — because that is what `contact` holds.
    async def ever_reached(self, org: str, contact: str) -> bool:
        """Whether this contact has ever called or written to any agent of the org, any world."""
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

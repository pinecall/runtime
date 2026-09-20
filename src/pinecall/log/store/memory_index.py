"""The call index in memory: the same questions as the postgres statements, over a test's calls."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import median

from pinecall.log.facts import CallFacts
from pinecall.log.store.index import (
    A_DAY_S,
    AgentDay,
    Day,
    Found,
    PersonaRun,
    PersonaRuns,
    ThreadRow,
    Threads,
    Unsealed,
    Wanted,
    after_the_cursor,
    digits_of,
    thread_cursor,
)

# What a call before any corner was written reads as: production's, the org's own (0024).
UNCORNERED = "production"


@dataclass(frozen=True)
class Indexed:
    """One call as the index reads it: whose, when, whether it is still open, and its facts."""

    org: str | None
    env: str | None
    holder: str | None
    started_at: float | None
    live: bool
    facts: CallFacts

    def of(self, org: str, env: str | None = None, holder: str | None = None) -> bool:
        """Whether this call is in that org, and in that world and corner when they are named."""
        return (
            self.org == org
            and (env is None or (self.env or UNCORNERED) == env)
            and (holder is None or (self.holder or "") == holder)
        )

    @property
    def at(self) -> float:
        """The clock a list orders by: a call nobody stamped sorts last."""
        return -1.0 if self.started_at is None else self.started_at


# What the store hands the reaper's question about each of its open logs. `Indexed` cannot answer
# it: it carries the facts and the corner, and this one is about the log's own clock.
@dataclass(frozen=True)
class StillOpen:
    """One call whose head row has not sealed: when it opened, when it last said anything."""

    call: str
    agent: str
    started_at: float
    last_at: float
    spoken: bool


def unsealed_spoken(calls: Iterable[StillOpen], quiet_since: float, limit: int) -> list[Unsealed]:
    """The spoken ones that have said nothing since then, quietest first — as the statement does."""
    quiet = sorted(
        (one for one in calls if one.spoken and one.last_at < quiet_since),
        key=lambda one: (one.last_at, one.call),
    )
    return [
        Unsealed(call=one.call, agent=one.agent, started_at=one.started_at, last_at=one.last_at)
        for one in quiet[:limit]
    ]


def found(calls: Iterable[Indexed], wanted: Wanted, limit: int) -> Found:
    """The page, the count and the cursor, exactly as the postgres statement answers them."""
    matching = sorted(
        (one for one in calls if _wanted(one, wanted)),
        key=lambda one: (one.at, one.facts.call),
        reverse=True,
    )
    below = next((one for one in matching if one.facts.call == wanted.before), None)
    page = [
        one
        for one in matching
        if below is None or (one.at, one.facts.call) < (below.at, below.facts.call)
    ]
    cut = page[:limit]
    more = len(page) > limit
    return Found(
        calls=[one.facts.call for one in cut],
        total=len(matching),
        next=cut[-1].facts.call if more and cut else None,
    )


def runs_of_persona(
    calls: Iterable[Indexed], persona: str, before: str | None, limit: int
) -> PersonaRuns:
    """The caller's runs, the page, the count and the cursor — exactly as the statement answers."""
    matching = sorted(
        (one for one in calls if one.facts.persona == persona),
        key=lambda one: (one.at, one.facts.call),
        reverse=True,
    )
    below = next((one for one in matching if one.facts.call == before), None)
    page = [
        one
        for one in matching
        if below is None or (one.at, one.facts.call) < (below.at, below.facts.call)
    ]
    cut = page[:limit]
    return PersonaRuns(
        runs=[PersonaRun(started_at=one.at, facts=one.facts) for one in cut],
        total=len(matching),
        next=cut[-1].facts.call if len(page) > limit and cut else None,
    )


def a_day(calls: Sequence[Indexed], start: float) -> Day:
    """The day's calls counted, and the two numbers that span every day: all, and still open."""
    today = [
        one
        for one in calls
        if one.started_at is not None and start <= one.started_at < start + A_DAY_S
    ]
    before = [
        one
        for one in calls
        if one.started_at is not None and start - A_DAY_S <= one.started_at < start
    ]
    finished = [one for one in today if one.facts.ended_at is not None]
    e2e = [value for one in today for value in one.facts.e2e]
    return Day(
        calls=len(today),
        yesterday=len(before),
        finished=len(finished),
        unescalated=sum(1 for one in finished if not one.facts.escalated),
        median_e2e=median(e2e) if e2e else None,
        spent=sum(one.facts.cost_eur or 0.0 for one in today),
        channels={
            channel: sum(1 for one in today if one.facts.channel == channel)
            for channel in ("phone", "web", "whatsapp")
        },
        agents=_agents(today),
        total=len(calls),
        live=sum(1 for one in calls if one.live),
    )


def threads(
    calls: Sequence[Indexed], read: dict[str, float], after: str | None, limit: int
) -> Threads:
    """The inbox, a contact per row, the thread that moved last first."""
    by_contact: dict[str, list[Indexed]] = {}
    for one in calls:
        if one.facts.contact is not None:
            by_contact.setdefault(one.facts.contact, []).append(one)
    rows = sorted(
        (_a_thread(contact, mine, read.get(contact, 0.0)) for contact, mine in by_contact.items()),
        key=lambda row: (row.moved_at, row.contact),
        reverse=True,
    )
    cursor = after_the_cursor(after)
    if cursor is not None:
        rows = [row for row in rows if (row.moved_at, row.contact) < cursor]
    page = rows[:limit]
    return Threads(rows=page, next=thread_cursor(page[-1]) if len(rows) > limit else None)


def moved_at(one: Indexed) -> float:
    """When a call last moved: its last turn, or when it started."""
    return one.facts.last_at if one.facts.last_at is not None else one.at


def _a_thread(contact: str, mine: list[Indexed], read_at: float) -> ThreadRow:
    newest = max(mine, key=moved_at)
    return ThreadRow(
        contact=contact,
        newest=newest.facts,
        moved_at=moved_at(newest),
        unread=sum(_unread(one, read_at) for one in mine),
        calls=len(mine),
        name=newest.facts.name or next((one.facts.name for one in mine if one.facts.name), None),
    )


def _unread(one: Indexed, read_at: float) -> int:
    """A spoken call is one thing to read; a written one is each message the contact sent."""
    if one.facts.spoken:
        return int(one.at > read_at)
    return sum(1 for at in one.facts.heard_at if at > read_at)


def _agents(today: Sequence[Indexed]) -> list[AgentDay]:
    slugs = sorted({one.facts.agent for one in today})
    days = [
        AgentDay(
            slug=slug,
            calls=sum(1 for one in today if one.facts.agent == slug),
            score=_held_rate([one.facts for one in today if one.facts.agent == slug]),
        )
        for slug in slugs
    ]
    return sorted(days, key=lambda day: (-day.calls, day.slug))


def _held_rate(facts: Sequence[CallFacts]) -> float | None:
    judged = [(one.held or 0) / one.judged for one in facts if one.judged]
    return sum(judged) / len(judged) if judged else None


def _wanted(one: Indexed, wanted: Wanted) -> bool:
    facts = one.facts
    if wanted.agent is not None and facts.agent != wanted.agent:
        return False
    if wanted.channel is not None and facts.channel != wanted.channel:
        return False
    return not wanted.q or _says(facts, wanted.q)


def _says(facts: CallFacts, q: str) -> bool:
    """The call id's start, a number's digits, the name, the outcome: any of them, any case."""
    words = q.casefold()
    digits = digits_of(q)
    numbers = (digits_of(facts.from_ or ""), digits_of(facts.to or ""))
    return (
        facts.call.casefold().startswith(words)
        or bool(digits and any(digits in number for number in numbers))
        or words in (facts.name or "").casefold()
        or words in (facts.outcome or "").casefold()
    )

"""The lists over every call's facts: calls, unsealed ones, threads, an inbox, days, runs."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from typing import LiteralString

from pinecall.domain.agent import Versions
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.log.facts import (
    A_DAY_S,
    CALLS_WITH,
    CORNER_OF_CALL,
    DAY,
    DAY_BY_AGENT,
    DAY_MEDIAN_E2E,
    EVER_REACHED,
    FACTS_OF,
    FOUND_COUNT,
    FOUND_PAGE,
    PERSONA_RUNS_COUNT,
    PERSONA_RUNS_PAGE,
    SPENT_BETWEEN,
    THREADS,
    UNSEALED_SPOKEN,
    UNSEALED_WRITTEN,
    CallFacts,
    CallScope,
    facts_of,
)
from pinecall.log.reduce import METERED_TYPES, UsageRow, usage_row
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.wire.parts import ToolResult

TOOL_ANSWERED = """
select data from call_log
where log = %(log)s and type = 'tool.result' and data->>'call_id' = %(call_id)s
order by seq
limit 1
"""

# A read cursor never moves back.
READ = """
insert into thread_reads as seen (org, env, holder, agent, reader, contact, read_at)
values (%(org)s, %(env)s, %(holder)s, %(agent)s, %(reader)s, %(contact)s, %(at)s)
on conflict (org, env, holder, agent, reader, contact)
do update set read_at = greatest(seen.read_at, excluded.read_at)
"""


@dataclass(frozen=True, slots=True)
class ListFilters:
    """What a call list asks for: an agent, a channel, words, and the page before this one."""

    agent: str | None = None
    # Matched case-insensitively against the call id's start, either number's digits, the
    # caller's name and the outcome.
    q: str | None = None
    channel: str | None = None
    before: str | None = None


@dataclass(frozen=True, slots=True)
class Found:
    """One page of calls, newest first, the total and the cursor of the next page."""

    calls: list[str]
    total: int
    next: str | None


@dataclass(frozen=True, slots=True)
class Unsealed:
    """A call still open, for the reaper: when it started, when it last moved, its channel."""

    call: str
    agent: str
    started_at: float
    last_at: float
    channel: str | None


@dataclass(frozen=True, slots=True)
class AgentDay:
    """One agent's calls in a day and the share of judgments it held."""

    slug: str
    calls: int
    score: float | None


@dataclass(frozen=True, slots=True)
class Day:
    """A scope's day in numbers."""

    calls: int
    yesterday: int
    finished: int
    unescalated: int
    median_e2e: float | None
    spent: float
    channels: dict[str, int]
    agents: list[AgentDay]
    total: int
    live: int


@dataclass(frozen=True, slots=True)
class InboxRow:
    """One contact in an inbox: its newest call, when it moved, what the reader has not read."""

    contact: str
    newest: CallFacts
    moved_at: float
    unread: int
    calls: int
    name: str | None


@dataclass(frozen=True, slots=True)
class InboxPage:
    """One page of an inbox and the cursor of the next."""

    rows: list[InboxRow]
    next: str | None


@dataclass(frozen=True, slots=True)
class Inbox:
    """Whose inbox: a scope, an agent and the person reading it."""

    scope: Scope
    agent: str
    reader: str


@dataclass(frozen=True, slots=True)
class PersonaRunFilters:
    """Whose runs a list asks for: the agent, the persona that called it, the page before this."""

    agent: str
    persona: str
    before: str | None = None


@dataclass(frozen=True, slots=True)
class PersonaRun:
    """One simulated call by a persona: when it started, its facts, how many turns it took."""

    started_at: float
    facts: CallFacts
    turns: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "turns", len(self.facts.heard_at))


@dataclass(frozen=True, slots=True)
class PersonaRuns:
    """One page of a persona's runs, newest first, the total and the cursor of the next page."""

    runs: list[PersonaRun]
    total: int
    next: str | None


@dataclass(frozen=True, slots=True)
class MeteredPage:
    """One page of the usage feed: the rows kept, and the cursor past everything read."""

    rows: list[UsageRow]
    next: int | None


# The cursor moves past every row read, kept or not, so a page filtered to one org still moves.
async def metered_page(store: Store, *, after: int, limit: int, org: str | None) -> MeteredPage:
    """The metered rows after the cursor, of one org or of every org, folded."""
    read = [usage_row(item) for item in await store.across(METERED_TYPES, after=after, limit=limit)]
    kept = [row for row in read if org is None or row.org == org]
    return MeteredPage(rows=kept, next=read[-1].cursor if read else None)


async def scope_of_call(pool: Pool, call: str) -> CallScope | None:
    """Return where the call was opened, or None for a call nobody wrote to or claimed."""
    async with pool.connection() as connection:
        row = await (await connection.execute(CORNER_OF_CALL, {"call": call})).fetchone()
    if row is None:
        return None
    env: Env = "sandbox" if row["env"] == "sandbox" else "production"
    return CallScope(
        scope=None if row["org"] is None else Scope(row["org"], env, row["holder"]),
        agent=row["agent"] or "",
        versions=Versions(config=row["config_version"], lexicon=row["lexicon_version"]),
        sealed=row["sealed"],
        started_at=row["started_at"],
        written=row["written"],
    )


# Read only by a tool's round trip: the log's primary key starts with `log`, so it reads one call.
async def tool_answered(pool: Pool, call: str, call_id: str) -> ToolResult | None:
    """The `tool.result` the call's log holds for this tool call id, or None."""
    wanted = {"log": call, "call_id": call_id}
    async with pool.connection() as connection:
        row = await (await connection.execute(TOOL_ANSWERED, wanted)).fetchone()
    return None if row is None else ToolResult.model_validate(row["data"])


async def facts_of_calls(pool: Pool, calls: Sequence[str]) -> dict[str, CallFacts]:
    """Return the facts of each of the calls that has a row."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(FACTS_OF, {"calls": list(calls)})).fetchall()
    return {str(row["call"]): facts_of(row) for row in rows}


# Across every org: the reaper's question. A call that never reached call.started is reaped
# whatever its channel.
async def unsealed_spoken(pool: Pool, quiet_since: float, *, limit: int) -> list[Unsealed]:
    """Return the spoken or never-started calls still open and quiet since then, oldest first."""
    return await _unsealed(pool, UNSEALED_SPOKEN, quiet_since, limit)


# A written call idles out where it runs; this finds the ones a restart left open.
async def unsealed_written(pool: Pool, quiet_since: float, *, limit: int) -> list[Unsealed]:
    """Return the written calls still open and quiet since then, with their channel."""
    return await _unsealed(pool, UNSEALED_WRITTEN, quiet_since, limit)


async def found(pool: Pool, scope: Scope, wanted: ListFilters, *, limit: int) -> Found:
    """Return one page of the scope's calls the filter matches, newest first, and their total."""
    params = {
        **asdict(scope),
        "agent": wanted.agent,
        "channel": wanted.channel,
        "words": None if wanted.q is None else _like_escaped(wanted.q),
        "digits": "".join(item for item in (wanted.q or "") if item.isdigit()),
    }
    async with pool.connection() as connection:
        total = await (await connection.execute(FOUND_COUNT, params)).fetchone()
        page = {**params, "before": wanted.before, "limit": limit + 1}
        rows = await (await connection.execute(FOUND_PAGE, page)).fetchall()
    calls = [str(row["call"]) for row in rows]
    return Found(
        calls=calls[:limit],
        total=0 if total is None else int(total["total"]),
        next=calls[limit - 1] if len(calls) > limit else None,
    )


async def runs_of_persona(
    pool: Pool, scope: Scope, wanted: PersonaRunFilters, *, limit: int
) -> PersonaRuns:
    """Return a page of the persona's calls to the agent in the scope, newest first, and a total."""
    params = {**asdict(scope), "agent": wanted.agent, "persona": wanted.persona}
    async with pool.connection() as connection:
        total = await (await connection.execute(PERSONA_RUNS_COUNT, params)).fetchone()
        page = {**params, "before": wanted.before, "limit": limit + 1}
        rows = await (await connection.execute(PERSONA_RUNS_PAGE, page)).fetchall()
    runs = [PersonaRun(started_at=float(row["started_at"]), facts=facts_of(row)) for row in rows]
    return PersonaRuns(
        runs=runs[:limit],
        total=0 if total is None else int(total["total"]),
        next=runs[limit - 1].facts.call if len(runs) > limit else None,
    )


async def counted_day(pool: Pool, scope: Scope, start: float) -> Day:
    """Return the scope's day that begins at start, in numbers."""
    params = {**asdict(scope), "start": start, "end": start + A_DAY_S}
    async with pool.connection() as connection:
        counted = await (await connection.execute(DAY, params)).fetchone()
        median = await (await connection.execute(DAY_MEDIAN_E2E, params)).fetchone()
        agents = await (await connection.execute(DAY_BY_AGENT, params)).fetchall()
    if counted is None:
        raise DeclarationRefused("a day counts, even an empty one")
    return Day(
        calls=int(counted["calls"]),
        yesterday=int(counted["yesterday"]),
        finished=int(counted["finished"]),
        unescalated=int(counted["unescalated"]),
        median_e2e=None if median is None else median["median"],
        spent=float(counted["spent"]),
        channels={door: int(counted[door]) for door in ("phone", "web", "whatsapp")},
        agents=[
            AgentDay(slug=row["slug"], calls=row["calls"], score=row["score"]) for row in agents
        ],
        total=int(counted["total"]),
        live=int(counted["live"]),
    )


async def spent_between(pool: Pool, org: str, start: float, end: float) -> float:
    """Return what the org's calls started in [start, end) cost, every env and holder counted."""
    params = {"org": org, "start": start, "end": end}
    async with pool.connection() as connection:
        row = await (await connection.execute(SPENT_BETWEEN, params)).fetchone()
    return 0.0 if row is None else float(row["spent"])


async def threads(pool: Pool, inbox: Inbox, *, after: str | None, limit: int) -> InboxPage:
    """Return one page of the inbox, the contact that moved last first, with the unread counts."""
    moved_at, contact = _after_the_cursor(after)
    params = {
        **asdict(inbox.scope),
        "agent": inbox.agent,
        "reader": inbox.reader,
        "moved_at": moved_at,
        "contact": contact,
        "limit": limit + 1,
    }
    async with pool.connection() as connection:
        rows = await (await connection.execute(THREADS, params)).fetchall()
    found = [
        InboxRow(
            contact=str(row["contact"]),
            newest=facts_of(row),
            moved_at=float(row["moved_at"]),
            unread=int(row["unread"] or 0),
            calls=int(row["calls"]),
            name=row["known_as"],
        )
        for row in rows
    ]
    last = found[limit - 1] if len(found) > limit else None
    return InboxPage(
        rows=found[:limit], next=None if last is None else f"{last.moved_at!r}:{last.contact}"
    )


async def calls_with(
    pool: Pool, scope: Scope, agent: str, contact: str, *, limit: int
) -> list[str]:
    """Return the contact's newest calls with the agent in the scope."""
    params = {**asdict(scope), "agent": agent, "contact": contact, "limit": limit}
    async with pool.connection() as connection:
        rows = await (await connection.execute(CALLS_WITH, params)).fetchall()
    return [str(row["call"]) for row in rows]


async def ever_reached(pool: Pool, org: str, env: Env, contact: str) -> bool:
    """Return whether the contact ever had a call with any agent of the org in the world."""
    params = {"org": org, "env": env, "contact": contact}
    async with pool.connection() as connection:
        row = await (await connection.execute(EVER_REACHED, params)).fetchone()
    return row is not None and bool(row["reached"])


async def read(pool: Pool, inbox: Inbox, contact: str, at: float) -> None:
    """Mark the contact's thread read up to that time for this reader."""
    params = {
        **asdict(inbox.scope),
        "agent": inbox.agent,
        "reader": inbox.reader,
        "contact": contact,
        "at": at,
    }
    async with pool.connection() as connection:
        await connection.execute(READ, params)


async def _unsealed(
    pool: Pool, query: LiteralString, quiet_since: float, limit: int
) -> list[Unsealed]:
    async with pool.connection() as connection:
        cursor = await connection.execute(query, {"quiet_since": quiet_since, "limit": limit})
        rows = await cursor.fetchall()
    return [
        Unsealed(
            call=str(row["call"]),
            agent=str(row["agent"] or ""),
            started_at=float(row["started_at"]),
            last_at=float(row["last_at"]),
            channel=row["channel"],
        )
        for row in rows
    ]


def _after_the_cursor(cursor: str | None) -> tuple[float | None, str | None]:
    if cursor is None or ":" not in cursor:
        return None, None
    moved_at, contact = cursor.split(":", 1)
    try:
        return float(moved_at), contact
    except ValueError:
        return None, None


def _like_escaped(words: str) -> str:
    return words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

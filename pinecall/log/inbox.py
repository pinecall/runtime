"""A contact's threads with an agent: the inbox a person reads, what is unread, who was reached."""

from dataclasses import asdict, dataclass

from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.log.facts import (
    CALLS_WITH,
    EVER_REACHED,
    THREADS,
    CallFacts,
    facts_of,
)
from pinecall.postgres.pool import Pool

# A read cursor never moves back.
READ = """
insert into thread_reads as seen (org, env, holder, agent, reader, contact, read_at)
values (%(org)s, %(env)s, %(holder)s, %(agent)s, %(reader)s, %(contact)s, %(at)s)
on conflict (org, env, holder, agent, reader, contact)
do update set read_at = greatest(seen.read_at, excluded.read_at)
"""


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


def _after_the_cursor(cursor: str | None) -> tuple[float | None, str | None]:
    if cursor is None or ":" not in cursor:
        return None, None
    moved_at, contact = cursor.split(":", 1)
    try:
        return float(moved_at), contact
    except ValueError:
        return None, None

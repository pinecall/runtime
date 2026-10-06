"""A contact's threads with an agent: the inbox a person reads, what is unread, who was reached."""

from dataclasses import asdict, dataclass

from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.log.facts import CallFacts, facts_of
from pinecall.postgres.pool import Pool

# Unread for the reader: one per spoken call started since they read, one per line the contact
# wrote since then in a written call.
THREADS = """
with mine as (
    select f.*, head.agent, coalesce(head.started_at, -1) as at,
           coalesce(f.last_at, head.started_at, -1) as moved_at
    from call_log_head head join call_facts f on f.call = head.log
    where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
      and head.agent = %(agent)s and head.call is not null and f.contact is not null
), newest as (
    select distinct on (contact) *
    from mine order by contact, moved_at desc, call desc
), counted as (
    select mine.contact, count(*) as calls, max(mine.name) as any_name,
           sum(case when mine.spoken then (mine.at > coalesce(seen.read_at, 0))::int
                    else (select count(*) from unnest(mine.heard_at) as heard
                          where heard > coalesce(seen.read_at, 0))::int end) as unread
    from mine
    left join thread_reads seen
      on seen.org = %(org)s and seen.env = %(env)s and seen.holder = %(holder)s
     and seen.agent = %(agent)s and seen.reader = %(reader)s and seen.contact = mine.contact
    group by mine.contact
)
select newest.*, counted.calls, counted.unread, coalesce(newest.name, counted.any_name) as known_as
from newest join counted on counted.contact = newest.contact
where %(moved_at)s::double precision is null
   or (newest.moved_at, newest.contact) < (%(moved_at)s, %(contact)s::text)
order by newest.moved_at desc, newest.contact desc
limit %(limit)s
"""


CALLS_WITH = """
select head.log as call
from call_log_head head join call_facts f on f.call = head.log
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.agent = %(agent)s and head.call is not null and f.contact = %(contact)s
order by coalesce(head.started_at, -1) desc, head.log desc
limit %(limit)s
"""


# Any agent of the org, on purpose: a past contact allows a call back. In one world: a test call
# from a phone in the sandbox never makes it dialable from production. Matched as stored.
EVER_REACHED = """
select exists (
    select 1 from call_log_head head join call_facts f on f.call = head.log
    where head.org = %(org)s and head.env = %(env)s and head.call is not null
      and f.contact = %(contact)s
) as reached
"""


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

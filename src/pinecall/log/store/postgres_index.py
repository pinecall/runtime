"""The call index in Postgres: the fold's upsert on append, and the questions asked across calls."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pinecall.log.facts import CallFacts, Change
from pinecall.log.store.index import (
    A_DAY_S,
    AgentDay,
    Day,
    Found,
    ThreadRow,
    Threads,
    Wanted,
    after_the_cursor,
    digits_of,
    like_escaped,
    thread_cursor,
)
from pinecall.log.store.index_statements import (
    CALLS_WITH,
    DAY,
    DAY_BY_AGENT,
    DAY_MEDIAN_E2E,
    FACTS_CHANGED,
    FACTS_OF,
    FOUND_COUNT,
    FOUND_PAGE,
    READ,
    SPENT_BETWEEN,
    THREADS,
)


# A mixin and not a second store: the index is written in the very append the log is, off the one
# pool the store holds, and a door reads both off the one object the lifespan opened.
class PostgresIndex:
    """The CallIndex verbs, for a store that holds `_pool`."""

    _pool: Any

    async def _fold(self, call: str, change: Change) -> None:
        """One entry's change onto its call's row."""
        await self._pool.execute(
            FACTS_CHANGED,
            call,
            change.channel,
            change.direction,
            change.from_,
            change.to,
            change.name,
            change.contact,
            change.spoken,
            change.ended_at,
            change.end_reason,
            change.outcome,
            change.cost_eur,
            change.scored,
            change.judged,
            change.held,
            change.passed,
            change.reason,
            change.promised,
            change.escalated,
            list(change.e2e),
            list(change.heard_at),
            change.last_text,
            change.last_at,
            change.last_in,
        )

    async def facts_of(self, calls: Sequence[str]) -> dict[str, CallFacts]:
        """The facts of each of these calls that has a row."""
        rows: Sequence[Any] = await self._pool.fetch(FACTS_OF, list(calls))
        return {str(row["call"]): facts_of_row(row) for row in rows}

    async def found(self, org: str, env: str, holder: str, wanted: Wanted, limit: int) -> Found:
        """The count and one page, off the same WHERE."""
        words = like_escaped(wanted.q) if wanted.q else None
        digits = digits_of(wanted.q or "")
        asked = (org, env, holder, wanted.agent, wanted.channel, words, digits)
        total = await self._pool.fetchval(FOUND_COUNT, *asked)
        rows: Sequence[Any] = await self._pool.fetch(FOUND_PAGE, *asked, wanted.before, limit + 1)
        calls = [str(row["call"]) for row in rows]
        page = calls[:limit]
        return Found(
            calls=page,
            total=int(total or 0),
            next=page[-1] if len(calls) > limit and page else None,
        )

    async def a_day(self, org: str, env: str, holder: str, start: float) -> Day:
        """Three reads: the counts, the median, and the agents."""
        asked = (org, env, holder, start, start + A_DAY_S)
        counted = await self._pool.fetchrow(DAY, *asked)
        median = await self._pool.fetchval(DAY_MEDIAN_E2E, *asked)
        agents: Sequence[Any] = await self._pool.fetch(DAY_BY_AGENT, *asked)
        return Day(
            calls=int(counted["calls"]),
            yesterday=int(counted["yesterday"]),
            finished=int(counted["finished"]),
            unescalated=int(counted["unescalated"]),
            median_e2e=None if median is None else float(median),
            spent=float(counted["spent"]),
            channels={door: int(counted[door]) for door in ("phone", "web", "whatsapp")},
            agents=[
                AgentDay(
                    slug=str(row["slug"]),
                    calls=int(row["calls"]),
                    score=None if row["score"] is None else float(row["score"]),
                )
                for row in agents
            ],
            total=int(counted["total"]),
            live=int(counted["live"]),
        )

    async def spent_between(self, org: str, start: float, end: float) -> float:
        """One sum over the org's calls in that span."""
        return float(await self._pool.fetchval(SPENT_BETWEEN, org, start, end) or 0.0)

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
        """One page of the inbox, and one row past it to know whether there is another."""
        cursor = after_the_cursor(after)
        rows: Sequence[Any] = await self._pool.fetch(
            THREADS,
            org,
            env,
            holder,
            agent,
            reader,
            None if cursor is None else cursor[0],
            None if cursor is None else cursor[1],
            limit + 1,
        )
        threads = [
            ThreadRow(
                contact=str(row["contact"]),
                newest=facts_of_row(row),
                moved_at=float(row["moved_at"]),
                unread=int(row["unread"] or 0),
                calls=int(row["calls"]),
                name=row["known_as"],
            )
            for row in rows
        ]
        page = threads[:limit]
        return Threads(rows=page, next=thread_cursor(page[-1]) if len(threads) > limit else None)

    async def calls_with(
        self, org: str, env: str, holder: str, agent: str, contact: str, limit: int
    ) -> list[str]:
        """The contact's newest calls with the agent."""
        rows: Sequence[Any] = await self._pool.fetch(
            CALLS_WITH, org, env, holder, agent, contact, limit
        )
        return [str(row["call"]) for row in rows]

    async def read(
        self, org: str, env: str, holder: str, agent: str, reader: str, contact: str, at: float
    ) -> None:
        """The reader's cursor, moved forward."""
        await self._pool.execute(READ, org, env, holder, agent, reader, contact, at)


def facts_of_row(row: Mapping[str, Any]) -> CallFacts:
    """One call_facts row, with its head row's agent, back into the fold's own shape."""
    return CallFacts(
        call=str(row["call"]),
        agent=str(row["agent"] or ""),
        channel=row["channel"],
        direction=row["direction"],
        from_=row["from_number"],
        to=row["to_number"],
        name=row["name"],
        contact=row["contact"],
        spoken=bool(row["spoken"]),
        ended_at=row["ended_at"],
        end_reason=row["end_reason"],
        outcome=row["outcome"],
        cost_eur=row["cost_eur"],
        judged=row["judged"],
        held=row["held"],
        passed=row["passed"],
        reason=row["reason"],
        escalated=bool(row["escalated"]),
        promised=bool(row["promised"]),
        e2e=tuple(row["e2e"] or ()),
        heard_at=tuple(row["heard_at"] or ()),
        last_text=row["last_text"],
        last_at=row["last_at"],
        last_in=row["last_in"],
    )

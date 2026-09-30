"""What an org used in a world, month by month: the totals admission reads, and their refold."""

from dataclasses import dataclass
from datetime import UTC, date, datetime

from psycopg.rows import DictRow

from pinecall.domain.names import Env, parse_env
from pinecall.log.reduce import Metered, Usage, usage_row
from pinecall.log.store import entry_of
from pinecall.postgres.pool import Pool, unbounded

# A quota is the month's: what the org used in the world in one calendar month, one row.
USED = """
SELECT calls, minutes, messages, input_tokens, output_tokens, characters, cost_usd
FROM usage_totals WHERE org = %(org)s AND env = %(env)s AND period = %(month)s
"""

# A budget is the org's in both worlds: a month's spend is every world's row of that month.
SPENT_IN = """
SELECT coalesce(sum(cost_usd), 0) AS spent FROM usage_totals
WHERE org = %(org)s AND period = %(month)s
"""

# The refold holds the table while it reads the log, so a summary written meanwhile waits and is
# counted after the table is rewritten, never twice and never lost.
HELD = "LOCK TABLE usage_totals IN EXCLUSIVE MODE"

SUMMARIES = """
SELECT head.org, head.env, entry.call, entry.seq, entry.ts, entry.agent, entry.type,
       entry.ephemeral, entry.data
FROM call_log entry JOIN call_log_head head ON head.log = entry.log
WHERE entry.type = 'call.summary' AND head.org IS NOT NULL AND head.env IS NOT NULL
"""

FORGOTTEN = "DELETE FROM usage_totals"

WRITTEN = """
INSERT INTO usage_totals
    (org, env, period, calls, minutes, messages, input_tokens, output_tokens, characters, cost_usd)
VALUES (%(org)s, %(env)s, %(period)s, %(calls)s, %(minutes)s, %(messages)s, %(input_tokens)s,
        %(output_tokens)s, %(characters)s, %(cost_usd)s)
"""

ROWS = """
SELECT org, env, period, calls, minutes, messages, input_tokens, output_tokens, characters,
       cost_usd
FROM usage_totals
"""


@dataclass(frozen=True, order=True)
class Month:
    """Whose a row of the totals is: the org, the world, the calendar month (UTC)."""

    org: str
    env: Env
    period: date


async def used(pool: Pool, org: str, env: Env, month: date) -> Usage:
    """What the org's calls in the world consumed in the month: one row read."""
    params = {"org": org, "env": env, "month": month.replace(day=1)}
    async with pool.connection() as connection:
        row = await (await connection.execute(USED, params)).fetchone()
    return Usage() if row is None else _usage(row)


async def spent_in(pool: Pool, org: str, month: date) -> float:
    """What the org's calls in both worlds cost in the month their summaries were written."""
    params = {"org": org, "month": month.replace(day=1)}
    async with pool.connection() as connection:
        row = await (await connection.execute(SPENT_IN, params)).fetchone()
    return 0.0 if row is None else float(row["spent"])


async def rebuild(pool: Pool) -> dict[Month, Usage]:
    """Refold the totals from every summary in the log, as the usage feed folds each one."""
    async with unbounded(pool) as connection:
        await connection.execute(HELD)
        rows = await (await connection.execute(SUMMARIES)).fetchall()
        refolded: dict[Month, Usage] = {}
        for row in rows:
            month = Month(str(row["org"]), parse_env(str(row["env"])), month_of(row["ts"]))
            consumed = usage_row(Metered(position=0, org=month.org, entry=entry_of(row))).used
            refolded[month] = refolded.get(month, Usage()) + consumed
        await connection.execute(FORGOTTEN)
        async with connection.cursor() as cursor:
            await cursor.executemany(
                WRITTEN,
                [
                    {**vars(month), **_columns(consumed)}
                    for month, consumed in sorted(refolded.items())
                ],
            )
    return refolded


async def totals(pool: Pool) -> dict[Month, Usage]:
    """Every row of the totals, as the database keeps them."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(ROWS)).fetchall()
    return {
        Month(str(row["org"]), parse_env(str(row["env"])), row["period"]): _usage(row)
        for row in rows
    }


def month_of(ts: float) -> date:
    """The calendar month, in UTC, a summary written at that time is counted in."""
    return datetime.fromtimestamp(ts, UTC).date().replace(day=1)


def _usage(row: DictRow) -> Usage:
    return Usage(
        calls=int(row["calls"]),
        minutes=float(row["minutes"]),
        messages=int(row["messages"]),
        input_tokens=int(row["input_tokens"]),
        output_tokens=int(row["output_tokens"]),
        characters=int(row["characters"]),
        cost_usd=float(row["cost_usd"]),
    )


# A summary's usage has no judge calls: they are its score's, which the totals do not count.
def _columns(used: Usage) -> dict[str, object]:
    return {
        "calls": used.calls,
        "minutes": used.minutes,
        "messages": used.messages,
        "input_tokens": used.input_tokens,
        "output_tokens": used.output_tokens,
        "characters": used.characters,
        "cost_usd": used.cost_usd,
    }

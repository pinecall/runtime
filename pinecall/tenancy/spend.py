"""An org that spends strangely: today against its own trailing four weeks, said once a day."""

from dataclasses import dataclass
from datetime import UTC, datetime

from pinecall.postgres.pool import Pool

# Today is unusual once it costs this many times the org's usual day, the mean of its trailing
# four weeks; a usual day under a dollar is too small to judge, so a new org is never flagged.
UNUSUAL_MULTIPLE = 3.0
USUAL_AT_LEAST_USD = 1.0
TRAILING_DAYS = 28

A_DAY_S = 86_400.0

# Both worlds and every holder: the dollars are the org's. Read off the facts, which the summary
# folded its cost into, by the day each call started.
SPENT = """
select coalesce(sum(f.cost_usd) filter (where head.started_at >= %(day)s), 0) as today,
       coalesce(sum(f.cost_usd) filter (where head.started_at < %(day)s), 0) as before
from call_log_head head join call_facts f on f.call = head.log
where head.org = %(org)s and head.call is not null and head.started_at >= %(day)s - %(trailing)s
"""

# Said once a day for the org: on whichever of its agents' logs the first unusual call sealed on,
# found by the entry's own org, so no head need have been claimed.
SAID_TODAY = """
select 1 from call_log entry
where entry.type = 'spend.unusual' and entry.ts >= %(day)s and entry.data ->> 'org' = %(org)s
limit 1
"""


@dataclass(frozen=True)
class Unusual:
    """Today's spend against the usual day, and how many times it is."""

    day: str
    today_usd: float
    usual_usd: float
    multiple: float


async def unusual(pool: Pool, org: str, at: float) -> Unusual | None:
    """Today's spend against the org's usual day, when it is over the line; None when usual."""
    day = day_of(at)
    params = {"org": org, "day": day, "trailing": TRAILING_DAYS * A_DAY_S}
    async with pool.connection() as connection:
        row = await (await connection.execute(SPENT, params)).fetchone()
    if row is None:
        return None
    today, usual = float(row["today"]), float(row["before"]) / TRAILING_DAYS
    if usual < USUAL_AT_LEAST_USD or today < UNUSUAL_MULTIPLE * usual:
        return None
    named = datetime.fromtimestamp(day, UTC).date().isoformat()
    return Unusual(named, round(today, 6), round(usual, 6), round(today / usual, 2))


async def said_today(pool: Pool, org: str, at: float) -> bool:
    """Whether spend.unusual was already written for the org today."""
    params = {"org": org, "day": day_of(at)}
    async with pool.connection() as connection:
        return await (await connection.execute(SAID_TODAY, params)).fetchone() is not None


def day_of(at: float) -> float:
    """The UTC midnight the moment falls after, as a timestamp."""
    moment = datetime.fromtimestamp(at, UTC)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

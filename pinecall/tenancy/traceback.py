"""A number's calls for a carrier's traceback: the calls kept, the calls erased, every dial."""

from dataclasses import dataclass
from datetime import datetime

from psycopg.rows import DictRow

from pinecall.domain.names import parse_e164
from pinecall.postgres.pool import Pool

# A kept call's facts, then an erased call's record: the same columns, oldest first.
CALLS = """
SELECT head.call, head.org, head.env, facts.direction, facts.from_number, facts.to_number,
       head.started_at, facts.ended_at, facts.end_reason, false AS erased
FROM call_facts facts JOIN call_log_head head ON head.call = facts.call
WHERE facts.channel = 'phone' AND head.started_at >= %(since)s
  AND (facts.from_number = %(number)s OR facts.to_number = %(number)s)
UNION ALL
SELECT call, org, env, direction, from_number, to_number, started_at, ended_at, end_reason, true
FROM call_records
WHERE started_at >= %(since)s AND (from_number = %(number)s OR to_number = %(number)s)
ORDER BY started_at
"""

# Every dial to the number, placed or refused, with who asked.
DIALS = """
SELECT org, env, agent, call, shown, asked_by, refused, at FROM dials
WHERE dialled = %(number)s AND at >= to_timestamp(%(since)s)
ORDER BY at
"""


@dataclass(frozen=True)
class CallRecord:
    """One phone call with the number: whose, which way, when, how it ended, whether erased."""

    call: str
    org: str | None
    env: str | None
    direction: str | None
    from_number: str | None
    to_number: str | None
    started_at: float | None
    ended_at: float | None
    end_reason: str | None
    erased: bool


@dataclass(frozen=True)
class DialRecord:
    """One dial to the number: whose, the call it placed, the number shown, who asked, the guard."""

    org: str
    env: str
    agent: str
    call: str | None
    shown: str | None
    asked_by: str
    refused: str | None
    at: float


@dataclass(frozen=True)
class Traceback:
    """What the box keeps of one number since a day."""

    number: str
    calls: list[CallRecord]
    dials: list[DialRecord]


async def of_number(pool: Pool, number: str, since: float) -> Traceback:
    """Every phone call with the number and every dial to it, since the epoch second given."""
    kept = parse_e164(number)
    params = {"number": kept, "since": since}
    async with pool.connection() as connection:
        calls = await (await connection.execute(CALLS, params)).fetchall()
        dials = await (await connection.execute(DIALS, params)).fetchall()
    return Traceback(kept, [CallRecord(**row) for row in calls], [_dial(row) for row in dials])


def _dial(row: DictRow) -> DialRecord:
    at: datetime = row["at"]
    return DialRecord(
        org=row["org"],
        env=row["env"],
        agent=row["agent"],
        call=row["call"],
        shown=row["shown"],
        asked_by=row["asked_by"],
        refused=row["refused"],
        at=at.timestamp(),
    )

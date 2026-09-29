"""A number's calls for a carrier's traceback: the calls kept, the calls erased, every dial."""

import time
from datetime import datetime

from psycopg.rows import DictRow

from pinecall.domain.names import parse_e164
from pinecall.postgres.pool import Pool
from pinecall.tenancy.retention import RECORDS_KEPT_S
from pinecall.wire.rest.ops import Traceback, TracebackCall, TracebackDial

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


async def of_number(pool: Pool, number: str, since: float | None = None) -> Traceback:
    """Every phone call with the number and every dial to it, since an epoch second or 24 months."""
    kept = parse_e164(number)
    start = time.time() - RECORDS_KEPT_S if since is None else since
    params = {"number": kept, "since": start}
    async with pool.connection() as connection:
        calls = await (await connection.execute(CALLS, params)).fetchall()
        dials = await (await connection.execute(DIALS, params)).fetchall()
    return Traceback(
        number=kept,
        since=start,
        calls=[TracebackCall.model_validate(row) for row in calls],
        dials=[_dial(row) for row in dials],
    )


def _dial(row: DictRow) -> TracebackDial:
    at: datetime = row["at"]
    return TracebackDial.model_validate({**row, "at": at.timestamp()})

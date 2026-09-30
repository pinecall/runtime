"""The log's days: a partition of call_log per UTC day, made ahead and dropped once emptied."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from psycopg import sql

from pinecall.postgres.pool import Pool

A_DAY_S = 86400

# Made a week ahead each night: a box whose nightly run stops has a week before the default fills.
AHEAD = 7

# Doctor speaks up while there are fewer days ahead than this.
FEWEST_AHEAD = 2

# A day is call_log_<YYYYMMDD>; the table as it was before the days is one partition of its own.
A_DAY = re.compile(r"^call_log_(\d{8})$")

BEFORE = "call_log_before_the_days"

DEFAULT = "call_log_default"

PARTITIONS = """
SELECT child.relname AS name, pg_get_expr(child.relpartbound, child.oid) AS bound
FROM pg_inherits link JOIN pg_class child ON child.oid = link.inhrelid
WHERE link.inhparent = 'call_log'::regclass
"""

# The upper bound a partition was made with, as pg_get_expr prints it.
UPPER = re.compile(r"TO \('?(-?[0-9.e+]+)'?\)")

# A partition made or dropped waits a second at most for its lock, and tries again the next night.
BRIEF = "SET LOCAL lock_timeout = '1s'"

MADE = "CREATE TABLE {day} PARTITION OF call_log FOR VALUES FROM ({start}) TO ({end})"

DEFAULT_HOLDS = "SELECT EXISTS (SELECT 1 FROM {default} WHERE ts >= %(start)s AND ts < %(end)s)"

HOLDS = "SELECT EXISTS (SELECT 1 FROM {day}) AS holds"

DROPPED = "DROP TABLE {day}"

IN_THE_DEFAULT = "SELECT count(*) AS rows FROM {default}"


@dataclass(frozen=True)
class Partition:
    """A partition of the log: its name, and the time it holds rows up to (None: the default)."""

    name: str
    upto: float | None


@dataclass(frozen=True)
class Kept:
    """What a night did to the days: made, dropped, refused for rows the default holds of them."""

    made: list[str]
    dropped: list[str]
    refused: list[str]


async def kept(pool: Pool, now: float) -> Kept:
    """Make the days ahead that are missing and drop the past days nothing is left in."""
    found = await _partitions(pool)
    made, refused = await _made_ahead(pool, found, now)
    return Kept(made=made, dropped=await _dropped(pool, found, now), refused=refused)


async def examined(pool: Pool, now: float) -> str | None:
    """What is wrong with the days: rows the default caught, or too few days made ahead."""
    troubles: list[str] = []
    async with pool.connection() as connection:
        query = sql.SQL(IN_THE_DEFAULT).format(default=sql.Identifier(DEFAULT))
        row = await (await connection.execute(query)).fetchone()
    caught = 0 if row is None else int(row["rows"])
    if caught:
        troubles.append(f"{caught} rows in {DEFAULT}: a day was missing when they were written")
    ahead = sum(
        1 for part in await _partitions(pool) if part.upto is not None and part.upto > _day_of(now)
    )
    if ahead < FEWEST_AHEAD:
        troubles.append(f"{ahead} days of the log made ahead: `retention run` makes {AHEAD}")
    return "; ".join(troubles) or None


async def _partitions(pool: Pool) -> list[Partition]:
    """Every partition of the log, the default's end unknown."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(PARTITIONS)).fetchall()
    found: list[Partition] = []
    for row in rows:
        upper = UPPER.search(str(row["bound"]))
        found.append(Partition(str(row["name"]), None if upper is None else float(upper.group(1))))
    return sorted(found, key=lambda partition: (partition.upto is None, partition.upto or 0.0))


def _day_named(start: float) -> str:
    """The name of the day that starts at that time."""
    return f"call_log_{datetime.fromtimestamp(start, UTC):%Y%m%d}"


# A day before the old table's bound is its; a day the default holds rows of cannot be made, since
# making it would move them, and is named instead.
async def _made_ahead(
    pool: Pool, found: list[Partition], now: float
) -> tuple[list[str], list[str]]:
    names = {part.name for part in found}
    before = next((part.upto for part in found if part.name == BEFORE), None) or 0.0
    made: list[str] = []
    refused: list[str] = []
    for start in (_day_of(now) + n * A_DAY_S for n in range(AHEAD)):
        name = _day_named(start)
        if name in names or start < before:
            continue
        bounds = {"start": start, "end": start + A_DAY_S}
        async with pool.connection() as connection, connection.transaction():
            await connection.execute(BRIEF)
            holds = sql.SQL(DEFAULT_HOLDS).format(default=sql.Identifier(DEFAULT))
            row = await (await connection.execute(holds, bounds)).fetchone()
            if row is not None and row["exists"]:
                refused.append(name)
                continue
            statement = sql.SQL(MADE).format(
                day=sql.Identifier(name), start=sql.Literal(start), end=sql.Literal(start + A_DAY_S)
            )
            await connection.execute(statement)
        made.append(name)
    return made, refused


# Only a day wholly past and empty goes: its rows went with the calls erasure took, one by one,
# as each org's days said; a row of a call kept, or of an agent's own log, keeps it.
async def _dropped(pool: Pool, found: list[Partition], now: float) -> list[str]:
    dropped: list[str] = []
    for part in found:
        if part.upto is None or part.upto > _day_of(now):
            continue
        async with pool.connection() as connection, connection.transaction():
            await connection.execute(BRIEF)
            holds = sql.SQL(HOLDS).format(day=sql.Identifier(part.name))
            row = await (await connection.execute(holds)).fetchone()
            if row is None or row["holds"]:
                continue
            await connection.execute(sql.SQL(DROPPED).format(day=sql.Identifier(part.name)))
        dropped.append(part.name)
    return dropped


def _day_of(now: float) -> float:
    return float(int(now) - int(now) % A_DAY_S)

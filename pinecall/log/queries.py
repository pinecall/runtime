"""One call's facts as a gateway asks them: where it opened, its tool calls, whether it sealed."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import LiteralString

from pinecall.domain.agent import Versions
from pinecall.domain.call import Opener
from pinecall.domain.names import Env, parse_env
from pinecall.domain.scope import Scope
from pinecall.log.facts import (
    CORNER_OF_CALL,
    FACTS_OF,
    UNSEALED_SPOKEN,
    UNSEALED_WRITTEN,
    CallFacts,
    CallScope,
    facts_of,
)
from pinecall.log.store import entry_of
from pinecall.postgres.pool import Pool
from pinecall.wire.frames import Entry
from pinecall.wire.parts import ToolResult

TOOL_ANSWERED = """
select data from call_log
where log = %(log)s and type = 'tool.result' and data->>'call_id' = %(call_id)s
order by seq
limit 1
"""

# A tool call id the log asked already: a retry that reached another gateway waits for its answer.
TOOL_CALLED = """
select call, seq, ts, agent, type, ephemeral, data from call_log
where log = %(log)s and type = 'tool.call' and data->>'call_id' = %(call_id)s
order by seq
limit 1
"""

# The head's word on who opened a call, read back as the type; anything else is nobody's.
OPENERS: dict[str, Opener] = {"fleet": "fleet", "app": "app", "gateway": "gateway"}


# Which of the calls a gateway serves another gateway sealed: one read of their heads.
SEALED_AMONG = """
select log from call_log_head where log = any(%(calls)s) and sealed
"""


@dataclass(frozen=True, slots=True)
class Unsealed:
    """A call still open, for the reaper: its start, its last move, its channel and its world."""

    call: str
    agent: str
    started_at: float
    last_at: float
    channel: str | None
    # None for a log no scope ever claimed.
    env: Env | None


async def sealed_among(pool: Pool, calls: list[str]) -> set[str]:
    """The ones of these calls whose log is sealed, wherever it was sealed."""
    if not calls:
        return set()
    async with pool.connection() as connection:
        rows = await (await connection.execute(SEALED_AMONG, {"calls": calls})).fetchall()
    return {str(row["log"]) for row in rows}


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
        opened_by=_opener(row["opened_by"]),
    )


# Read only by a tool's round trip: the log's primary key starts with `log`, so it reads one call.
async def tool_answered(pool: Pool, call: str, call_id: str) -> ToolResult | None:
    """The `tool.result` the call's log holds for this tool call id, or None."""
    wanted = {"log": call, "call_id": call_id}
    async with pool.connection() as connection:
        row = await (await connection.execute(TOOL_ANSWERED, wanted)).fetchone()
    return None if row is None else ToolResult.model_validate(row["data"])


async def tool_called(pool: Pool, call: str, call_id: str) -> Entry | None:
    """The `tool.call` entry the call's log holds for this tool call id, or None."""
    wanted = {"log": call, "call_id": call_id}
    async with pool.connection() as connection:
        row = await (await connection.execute(TOOL_CALLED, wanted)).fetchone()
    return None if row is None else entry_of(row)


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


def _opener(named: object) -> Opener | None:
    return OPENERS.get(named) if isinstance(named, str) else None


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
            env=None if row["env"] is None else parse_env(str(row["env"])),
        )
        for row in rows
    ]

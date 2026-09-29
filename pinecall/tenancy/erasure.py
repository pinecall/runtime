"""An erasure: a call's, a contact's or an org's rows and recordings gone, and its trail row."""

import asyncio
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from pinecall.domain.errors import StoreUnreachable
from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.wire.rest.calls import Erasure, ErasureSubject

# The trigger on call_log lets a DELETE through in this transaction alone (0011_erasure.sql).
ERASING = "SET LOCAL pinecall.erasing = 'on'"

CALLS_OF_CONTACT = """
SELECT head.call FROM call_log_head head JOIN call_facts facts ON facts.call = head.call
WHERE head.org = %(org)s AND head.env = %(env)s AND facts.contact = %(contact)s
"""

# Every log of the org: its calls, and its agents' own logs ("@<agent>"), which carry no call.
LOGS_OF_ORG = "SELECT log, call FROM call_log_head WHERE org = %(org)s"

# What a call left: its entries, its head, its facts, its tokens, the memories it taught. The
# dials ledger stays: it names numbers and times, never what was said, and a traceback asks for it.
ERASE_LOGS = """
WITH entries AS (DELETE FROM call_log WHERE log = ANY(%(logs)s) RETURNING 1),
     heads AS (DELETE FROM call_log_head WHERE log = ANY(%(logs)s) RETURNING 1),
     facts AS (DELETE FROM call_facts WHERE call = ANY(%(calls)s) RETURNING 1),
     spent AS (DELETE FROM tokens WHERE call = ANY(%(calls)s) RETURNING 1),
     taught AS (DELETE FROM contact_memories WHERE source_call = ANY(%(calls)s) RETURNING 1)
SELECT (SELECT count(*) FROM entries) AS entries, (SELECT count(*) FROM taught) AS memories
"""

# A contact's memories no erased call taught (written by hand, or by a call erased before), and
# what each reader had read of their thread.
ERASE_CONTACT = """
WITH kept AS (
    DELETE FROM contact_memories WHERE org = %(org)s AND env = %(env)s AND contact = %(contact)s
    RETURNING 1
), reads AS (
    DELETE FROM thread_reads WHERE org = %(org)s AND env = %(env)s AND contact = %(contact)s
)
SELECT count(*) AS memories FROM kept
"""

# The quotas, keys, carriers, settings and memories go by the foreign keys' cascade.
ERASE_ORG = "DELETE FROM orgs WHERE id = %(org)s"

TRAIL = """
INSERT INTO erasures (org, env, what, subject, asked_by, calls, entries, memories, recordings)
VALUES (%(org)s, %(env)s, %(what)s, %(subject)s, %(asked_by)s, %(calls)s, %(entries)s,
        %(memories)s, %(recordings)s)
RETURNING id, env, what, subject, asked_by, calls, entries, memories, recordings, at
"""

TRAIL_OF_ORG = """
SELECT id, env, what, subject, asked_by, calls, entries, memories, recordings, at
FROM erasures WHERE org = %(org)s ORDER BY at DESC, id DESC LIMIT %(limit)s
"""

NO_TRAIL = "the erasure of {subject} was not written down, so it was undone"


@dataclass(frozen=True)
class Erased:
    """An erasure done: its trail row, and the calls it took, for a process that holds them."""

    trail: Erasure
    calls: tuple[str, ...]


@dataclass(frozen=True)
class _Taking:
    """An erasure on its way: whose, what, who asked, and the rows gone so far."""

    org: str
    env: Env | None
    what: ErasureSubject
    subject: str
    asked_by: str
    calls: list[str]
    entries: int
    memories: int


async def call(pool: Pool, recordings: Path, scope: Scope, call_id: str, *, by: str) -> Erased:
    """Erase one call: its log, facts, tokens, the memories it taught, and its recording."""
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(ERASING)
        entries, memories = await _logs(connection, [call_id], [call_id])
        taking = _Taking(scope.org, scope.env, "call", call_id, by, [call_id], entries, memories)
        return await _written(connection, recordings, taking)


async def contact(
    pool: Pool, recordings: Path, scope: Scope, contact_id: str, *, by: str
) -> Erased:
    """Erase a contact in the world: every call they were on and every fact kept of them."""
    params = {"org": scope.org, "env": scope.env, "contact": contact_id}
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(ERASING)
        rows = await (await connection.execute(CALLS_OF_CONTACT, params)).fetchall()
        calls = [str(row["call"]) for row in rows]
        entries, taught = await _logs(connection, calls, calls)
        kept = await (await connection.execute(ERASE_CONTACT, params)).fetchone()
        memories = taught + (0 if kept is None else int(kept["memories"]))
        taking = _Taking(scope.org, scope.env, "contact", contact_id, by, calls, entries, memories)
        return await _written(connection, recordings, taking)


async def org(pool: Pool, recordings: Path, org_id: str, *, by: str) -> Erased:
    """Erase an org whole: every log it owns, every recording, then the org and what cascades."""
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(ERASING)
        rows = await (await connection.execute(LOGS_OF_ORG, {"org": org_id})).fetchall()
        logs = [str(row["log"]) for row in rows]
        calls = [str(row["call"]) for row in rows if row["call"] is not None]
        entries, memories = await _logs(connection, logs, calls)
        await connection.execute(ERASE_ORG, {"org": org_id})
        taking = _Taking(org_id, None, "org", org_id, by, calls, entries, memories)
        return await _written(connection, recordings, taking)


async def trail(pool: Pool, org_id: str, *, limit: int = 100) -> list[Erasure]:
    """The org's erasures, newest first."""
    params = {"org": org_id, "limit": limit}
    async with pool.connection() as connection:
        rows = await (await connection.execute(TRAIL_OF_ORG, params)).fetchall()
    return [_trail_row(row) for row in rows]


async def _logs(
    connection: AsyncConnection[DictRow], logs: list[str], calls: list[str]
) -> tuple[int, int]:
    row = await (await connection.execute(ERASE_LOGS, {"logs": logs, "calls": calls})).fetchone()
    return (0, 0) if row is None else (int(row["entries"]), int(row["memories"]))


# The files go inside the transaction, before its trail is written: a disk that refuses leaves
# the rows in place and the erasure failed, rather than a trail that says a file is gone.
async def _written(
    connection: AsyncConnection[DictRow], recordings: Path, taking: _Taking
) -> Erased:
    removed = await asyncio.to_thread(_removed, recordings, taking.calls)
    params = {**asdict(taking), "calls": len(taking.calls), "recordings": removed}
    row = await (await connection.execute(TRAIL, params)).fetchone()
    if row is None:
        raise StoreUnreachable(NO_TRAIL.format(subject=taking.subject))
    return Erased(trail=_trail_row(row), calls=tuple(taking.calls))


# A recording is a directory named for its call (worker/_recorder.py recording_path).
def _removed(recordings: Path, calls: list[str]) -> int:
    removed = 0
    for call_id in calls:
        directory = recordings / call_id
        if directory.is_dir():
            shutil.rmtree(directory)
            removed += 1
    return removed


def _trail_row(row: DictRow) -> Erasure:
    return Erasure.model_validate({**row, "at": row["at"].timestamp()})

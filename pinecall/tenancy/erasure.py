"""An erasure: a call's, a contact's or an org's rows and recordings gone, and its trail row."""

from dataclasses import asdict, dataclass

from psycopg import AsyncConnection
from psycopg.rows import DictRow

from pinecall.domain.errors import Conflict, StoreUnreachable
from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.process.recordings import Recordings
from pinecall.wire.rest.calls import Erasure, ErasureSubject

# The trigger on call_log lets a DELETE through in this transaction alone (0011_erasure.sql).
ERASING = "SET LOCAL pinecall.erasing = 'on'"

CALLS_OF_CONTACT = """
SELECT head.call, head.sealed
FROM call_log_head head JOIN call_facts facts ON facts.call = head.call
WHERE head.org = %(org)s AND head.env = %(env)s AND facts.contact = %(contact)s
"""

# Every log of the org: its calls, and its agents' own logs ("@<agent>"), which carry no call.
LOGS_OF_ORG = "SELECT log, call, agent FROM call_log_head WHERE org = %(org)s"

# What a call left: its entries and the private values sealed beside them, its head, its facts,
# its tokens, the memories it taught, its recording's key, the verdicts its day's drift counted
# (the day's sums keep its numbers, which name nobody), and the case of the org's dataset made of
# it (evals/dataset.py). A phone call's numbers, times and end stay in call_records, and the dials
# ledger stays: they name numbers and times, never what was said, and a traceback asks for them
# (0016_call_records.sql). Every statement of the WITH reads the rows as they were before it, so
# the record reads the facts.
ERASE_LOGS = """
WITH recorded AS (
    INSERT INTO call_records (call, org, env, direction, from_number, to_number, started_at,
                              ended_at, end_reason)
    SELECT facts.call, head.org, head.env, facts.direction, facts.from_number, facts.to_number,
           head.started_at, facts.ended_at, facts.end_reason
    FROM call_facts facts JOIN call_log_head head ON head.call = facts.call
    WHERE facts.call = ANY(%(calls)s) AND facts.channel = 'phone'
    ON CONFLICT (call) DO NOTHING
),
     entries AS (DELETE FROM call_log WHERE log = ANY(%(logs)s) RETURNING 1),
     private AS (DELETE FROM call_private WHERE log = ANY(%(logs)s)),
     keys AS (DELETE FROM recording_keys WHERE call = ANY(%(calls)s)),
     heads AS (DELETE FROM call_log_head WHERE log = ANY(%(logs)s) RETURNING 1),
     facts AS (DELETE FROM call_facts WHERE call = ANY(%(calls)s) RETURNING 1),
     spent AS (DELETE FROM tokens WHERE call = ANY(%(calls)s) RETURNING 1),
     taught AS (DELETE FROM contact_memories WHERE source_call = ANY(%(calls)s) RETURNING 1),
     drifted AS (DELETE FROM drift_calls WHERE call = ANY(%(calls)s)),
     promoted AS (DELETE FROM eval_cases WHERE source_call = ANY(%(calls)s))
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

# What has no foreign key to the org: its agents' eval runs and hold melodies, what each reader
# read of a thread, and its keys, revoked ones included. Then the org, and the quotas, carriers,
# settings and memories cascade.
ERASE_ORG = """
WITH runs AS (DELETE FROM eval_runs WHERE agent = ANY(%(agents)s)),
     melodies AS (DELETE FROM hold_audio WHERE org = %(org)s),
     reads AS (DELETE FROM thread_reads WHERE org = %(org)s),
     keys AS (DELETE FROM api_keys WHERE org = %(org)s)
DELETE FROM orgs WHERE id = %(org)s
"""

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

STILL_ON_A_CALL = "{contact} is on call {call} right now: erase them once it has ended"


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


async def call(
    pool: Pool, recordings: Recordings, scope: Scope, call_id: str, *, by: str
) -> Erased:
    """Erase one call: its log, facts, tokens, the memories it taught, and its recording."""
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(ERASING)
        entries, memories = await _logs(connection, [call_id], [call_id])
        taking = _Taking(scope.org, scope.env, "call", call_id, by, [call_id], entries, memories)
        return await _written(connection, recordings, taking)


async def contact(
    pool: Pool, recordings: Recordings, scope: Scope, contact_id: str, *, by: str
) -> Erased:
    """Erase a contact in the world: every call they were on and every fact kept of them."""
    params = {"org": scope.org, "env": scope.env, "contact": contact_id}
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(ERASING)
        rows = await (await connection.execute(CALLS_OF_CONTACT, params)).fetchall()
        live = next((str(row["call"]) for row in rows if not row["sealed"]), None)
        if live is not None:
            raise Conflict(STILL_ON_A_CALL.format(contact=contact_id, call=live))
        calls = [str(row["call"]) for row in rows]
        entries, taught = await _logs(connection, calls, calls)
        kept = await (await connection.execute(ERASE_CONTACT, params)).fetchone()
        memories = taught + (0 if kept is None else int(kept["memories"]))
        taking = _Taking(scope.org, scope.env, "contact", contact_id, by, calls, entries, memories)
        return await _written(connection, recordings, taking)


async def org(pool: Pool, recordings: Recordings, org_id: str, *, by: str) -> Erased:
    """Erase an org whole: every log it owns, every recording, then the org and what cascades."""
    async with pool.connection() as connection, connection.transaction():
        await connection.execute(ERASING)
        rows = await (await connection.execute(LOGS_OF_ORG, {"org": org_id})).fetchall()
        logs = [str(row["log"]) for row in rows]
        calls = [str(row["call"]) for row in rows if row["call"] is not None]
        agents = sorted({str(row["agent"]) for row in rows if row["agent"] is not None})
        entries, memories = await _logs(connection, logs, calls)
        await connection.execute(ERASE_ORG, {"org": org_id, "agents": agents})
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


# The recordings go inside the transaction, before its trail is written: a disk or a bucket
# that refuses leaves the rows in place and the erasure failed, not a trail that lies.
async def _written(
    connection: AsyncConnection[DictRow], recordings: Recordings, taking: _Taking
) -> Erased:
    removed = await recordings.erase(taking.org, taking.calls)
    params = {**asdict(taking), "calls": len(taking.calls), "recordings": removed}
    row = await (await connection.execute(TRAIL, params)).fetchone()
    if row is None:
        raise StoreUnreachable(NO_TRAIL.format(subject=taking.subject))
    return Erased(trail=_trail_row(row), calls=tuple(taking.calls))


def _trail_row(row: DictRow) -> Erasure:
    return Erasure.model_validate({**row, "at": row["at"].timestamp()})

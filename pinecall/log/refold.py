"""The facts folded again from the log: rebuilt a call at a time, and a sample checked by doctor."""

from dataclasses import asdict, dataclass, fields
from uuid import uuid4

from pinecall.domain.call import A_CALL
from pinecall.log import facts
from pinecall.log.facts import CallFacts
from pinecall.log.store import entry_of
from pinecall.postgres.pool import Connection, Pool

# Calls named per read of the heads; each is refolded in a transaction of its own, so a rebuild
# never holds a lock longer than one call's fold.
A_PAGE = 500


# What doctor refolds and compares, and how many of the heads behind their rows it names.
A_SAMPLE = 20


HEADS_EXAMINED = 20


CALLS_PAGE = """
select log from call_log_head
where call is not null and log > %(after)s
  and (%(call)s::text is null or log = %(call)s)
  and (%(org)s::text is null or org = %(org)s)
  and (%(since)s::float8 is null or started_at >= %(since)s)
order by log
limit %(limit)s
"""


# The append's own lock: an append waits for the refold, and the refold reads what it wrote.
HEAD_LOCKED = "select log from call_log_head where log = %(call)s for update"


# Snapshot reads: an append writes its entries and its facts in one transaction, so one snapshot
# sees both or neither.
SNAPSHOT = "set transaction isolation level repeatable read, read only"


ENTRIES = """
select call, seq, ts, agent, type, ephemeral, data
from call_log where log = %(call)s order by seq
"""


FACTS = "select * from call_facts where call = %(call)s"


# The heads doctor examines: the newest calls by the log's own order (the index of an entry's
# type and position finds the last call.started without reading the rest), and as many from a
# random point of the heads' key, round to the start. Each is one probe of the log's primary key:
# a head is never behind a row it numbered. Every head would be a probe per call on the box.
HEADS_BEHIND = """
with newest as (
    select log from call_log where type = 'call.started' order by position desc limit %(limit)s
), sampled as (
    (select log from call_log_head where log >= %(start)s order by log limit %(limit)s)
    union all
    (select log from call_log_head where log < %(start)s order by log limit %(limit)s)
    limit %(limit)s
), examined as (select log from newest union select log from sampled)
select examined.log,
       head.seq < (select max(entry.seq) from call_log entry where entry.log = examined.log)
           as behind
from examined join call_log_head head on head.log = examined.log
order by examined.log
"""


# From a random point of the call ids, round to the start: a sample the primary key finds.
SAMPLE = """
(select log from call_log_head where call is not null and sealed and log >= %(start)s
 order by log limit %(limit)s)
union all
(select log from call_log_head where call is not null and sealed and log < %(start)s
 order by log limit %(limit)s)
limit %(limit)s
"""


@dataclass(frozen=True)
class Refolding:
    """Which calls a rebuild refolds: one, an org's, those started since; every call when none."""

    call: str | None = None
    org: str | None = None
    since: float | None = None


@dataclass(frozen=True)
class Rebuilt:
    """What a rebuild read and what it rewrote."""

    calls: int
    rewritten: int


@dataclass(frozen=True)
class Heads:
    """The heads doctor examined, and those among them whose rows are past their head's seq."""

    examined: int
    behind: tuple[str, ...]


@dataclass(frozen=True)
class Differs:
    """A call whose stored facts are not what its log folds to, and the columns that differ."""

    call: str
    columns: tuple[str, ...]


async def rebuild(pool: Pool, refolding: Refolding, *, page: int = A_PAGE) -> Rebuilt:
    """Fold each call's facts again from its log and write the row where it differs."""
    calls = rewritten = 0
    after = ""
    while names := await _calls_after(pool, refolding, after, page):
        for call in names:
            async with pool.connection() as connection, connection.transaction():
                await connection.execute(HEAD_LOCKED, {"call": call})
                stored, folded = await _refolded(connection, call)
                if folded != (stored or CallFacts(call=call)):
                    await connection.execute(facts.FACTS_WRITTEN, _row(folded))
                    rewritten += 1
            calls += 1
        after = names[-1]
    return Rebuilt(calls=calls, rewritten=rewritten)


async def heads_behind(pool: Pool, *, examined: int = HEADS_EXAMINED) -> Heads:
    """The newest heads and a sample of the rest, and those that gave out fewer seqs than rows."""
    params = {"limit": examined, "start": f"{A_CALL}{uuid4().hex}"}
    async with pool.connection() as connection:
        rows = await (await connection.execute(HEADS_BEHIND, params)).fetchall()
    return Heads(examined=len(rows), behind=tuple(str(row["log"]) for row in rows if row["behind"]))


async def differing(pool: Pool, *, sample: int = A_SAMPLE) -> list[Differs]:
    """Refold a sample of sealed calls and name each whose stored facts differ, and where."""
    start = f"{A_CALL}{uuid4().hex}"
    async with pool.connection() as connection:
        rows = await (
            await connection.execute(SAMPLE, {"start": start, "limit": sample})
        ).fetchall()
    found: list[Differs] = []
    for call in (str(row["log"]) for row in rows):
        async with pool.connection() as connection, connection.transaction():
            await connection.execute(SNAPSHOT)
            stored, folded = await _refolded(connection, call)
        columns = _columns_apart(stored or CallFacts(call=call), folded)
        if columns:
            found.append(Differs(call=call, columns=columns))
    return found


async def _calls_after(pool: Pool, refolding: Refolding, after: str, page: int) -> list[str]:
    params = {**asdict(refolding), "after": after, "limit": page}
    async with pool.connection() as connection:
        rows = await (await connection.execute(CALLS_PAGE, params)).fetchall()
    return [str(row["log"]) for row in rows]


# The fold record() runs as entries land, run over the whole log from nothing.
async def _refolded(connection: Connection, call: str) -> tuple[CallFacts | None, CallFacts]:
    row = await (await connection.execute(FACTS, {"call": call})).fetchone()
    stored = None if row is None else facts.facts_of(row)
    folded = CallFacts(call=call)
    for entry in await (await connection.execute(ENTRIES, {"call": call})).fetchall():
        folded = facts.fold(folded, entry_of(entry))
    return stored, folded


def _row(folded: CallFacts) -> dict[str, object]:
    written: dict[str, object] = {
        **asdict(folded),
        "e2e": list(folded.e2e),
        "heard_at": list(folded.heard_at),
    }
    written.pop("agent")
    return written


def _columns_apart(stored: CallFacts, folded: CallFacts) -> tuple[str, ...]:
    return tuple(
        field.name
        for field in fields(CallFacts)
        if field.name in facts.COLUMNS
        and getattr(stored, field.name) != getattr(folded, field.name)
    )

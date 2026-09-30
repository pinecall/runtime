"""The writer: every log's appends written a group per transaction, fed ones in a lane apart."""

import asyncio
import logging
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, LiteralString

import psycopg
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from pinecall.domain.errors import Conflict
from pinecall.domain.names import JsonObject
from pinecall.log.facts import record
from pinecall.log.reduce import METERED_TYPES
from pinecall.postgres.pool import Connection, Pool
from pinecall.wire.frames import Entry

logger = logging.getLogger(__name__)

# The most entries one group carries.
MOST_IN_A_GROUP = 500

# The types read across every log by position: the usage feed, the callbacks, the codes, and
# WhatsApp's queue. A position is given at insert, not at commit, so a transaction that writes one
# of these takes FEED_ORDER first and holds it to its commit: their positions come in commit order,
# and a reader whose cursor passed one has seen every one before it.
FED_TYPES = frozenset(
    {
        *METERED_TYPES,
        "code.issued",
        "code.claimed",
        "callback.requested",
        "message.waiting",
        "message.taken",
    }
)

# Outside the 32 bits of hashtext, which keys the runtime's other advisory locks.
FEED_ORDER_KEY = 0x9E3_C411_FEED

FEED_ORDER = "select pg_advisory_xact_lock(%(key)s)"

# Locked in the order of their names, so two writers never wait on each other's rows in a circle.
HEADS_LOCKED = """
select log, seq, sealed, written, written_seq from call_log_head
where log = any(%(logs)s)
order by log
for update
"""

# A head at seq 0 for each log that begins in this group, rare beside the ones already there. One
# another writer made first is locked and read as it is: the update changes nothing of it.
HEADS_BEGUN = """
insert into call_log_head as head (log, agent, call)
select begun.log, begun.agent, begun.call
from unnest(%(logs)s::text[], %(agents)s::text[], %(calls)s::text[]) as begun(log, agent, call)
order by begun.log
on conflict (log) do update set log = head.log
returning log, seq, sealed, written, written_seq
"""

# The heads moved and the durable rows written in one statement: two tables, nothing between them
# to read.
WRITTEN = """
with moved as (
    update call_log_head as head
    set seq         = moved.seq,
        written     = moved.written,
        written_seq = moved.written_seq,
        agent       = coalesce(head.agent, moved.agent),
        call        = coalesce(head.call, moved.call),
        started_at  = coalesce(head.started_at, moved.started_at)
    from unnest(%(logs)s::text[], %(seqs)s::bigint[], %(written)s::bigint[],
                %(written_seqs)s::bigint[], %(agents)s::text[], %(heads_calls)s::text[],
                %(heads_stamps)s::float8[])
         as moved(log, seq, written, written_seq, agent, call, started_at)
    where head.log = moved.log
)
insert into call_log (call, seq, ts, agent, type, ephemeral, data)
select kept.call, kept.seq, kept.ts, kept.agent, kept.type, false, kept.data
from unnest(%(calls)s::text[], %(row_seqs)s::bigint[], %(stamps)s::float8[],
            %(row_agents)s::text[], %(types)s::text[], %(data)s::jsonb[])
     as kept(call, seq, ts, agent, type, data)
"""

# The first-of-its-type requests whose log already holds an entry of that type: read under the
# heads' locks, so a second writer deciding the same end sees the first one's row.
HELD_TYPES = """
select distinct wanted.log
from unnest(%(logs)s::text[], %(types)s::text[]) as wanted(log, type)
where exists (select 1 from call_log where log = wanted.log and type = wanted.type)
"""

# The answers of a tool call not needed: the log never asked that call id, or already holds its
# answer (the app's, or the timeout another gateway wrote). Read under the heads' locks, so of two
# gateways answering one call id the second sees the first's row.
UNANSWERABLE = """
select wanted.log
from unnest(%(logs)s::text[], %(ids)s::text[]) as wanted(log, call_id)
where not exists (select 1 from call_log where log = wanted.log and type = 'tool.call'
                    and data ->> 'call_id' = wanted.call_id)
   or exists (select 1 from call_log where log = wanted.log and type = 'tool.result'
                and data ->> 'call_id' = wanted.call_id)
"""

# A request that writes a fed type goes in the fed lane, which alone takes FEED_ORDER; every other
# goes in the plain lane, which never waits on it.
type Lane = Literal["fed", "plain"]

# The gateway's own entry, a worker's batch, a verdict written again on a sealed log, or a durable
# entry written only when its log is open and holds none of its type.
type Kind = Literal["entry", "batch", "score", "first", "answer"]


@dataclass(frozen=True, slots=True)
class Unnumbered:
    """An entry the log has not numbered yet: what it is, whether a store keeps it, and when."""

    type: str
    data: JsonObject
    ephemeral: bool
    ts: float


@dataclass(frozen=True, slots=True)
class Batch:
    """A request's entries as the log numbered them, and whether the log had taken them before."""

    entries: list[Entry]
    replayed: bool


@dataclass(frozen=True, slots=True)
class Append:
    """One request to the writer: its kind, its log, its entries stamped, and a batch's `after`."""

    kind: Kind
    log: str
    call: str | None
    agent: str
    entries: Sequence[Unnumbered]
    after: int = 0


@dataclass(frozen=True, slots=True)
class _Queued:
    """A request waiting for its group, its lane, and where its answer goes."""

    append: Append
    lane: Lane
    answer: asyncio.Future[Batch]


@dataclass(frozen=True, slots=True)
class _Moved:
    """A request the log takes: its head as it will be, and the first seq of its entries."""

    first: int
    seq: int
    written: int
    written_seq: int | None


class Writer:
    """The appends not yet written, in the order they came, and a task per lane writing them."""

    def __init__(self, pool: Pool) -> None:
        """Keep the pool; a lane's task starts with its first request and ends when none is left."""
        self.pool = pool
        self._queue: deque[_Queued] = deque()
        # The logs a lane's transaction writes now: the other lane leaves them until it commits.
        self._writing: set[str] = set()
        self._tasks: dict[Lane, asyncio.Task[None]] = {}

    @property
    def waiting(self) -> int:
        """How many requests wait for a transaction now: what /metrics says a writer is behind."""
        return len(self._queue)

    async def written(self, append: Append) -> Batch:
        """Queue the request; its entries numbered, once the transaction that wrote them commits."""
        answer: asyncio.Future[Batch] = asyncio.get_running_loop().create_future()
        self._queue.append(_Queued(append, "fed" if _fed(append) else "plain", answer))
        self._started()
        return await answer

    async def drained(self) -> None:
        """Return once every request queued so far is written and answered."""
        while running := [task for task in self._tasks.values() if not task.done()]:
            await asyncio.shield(asyncio.gather(*running))

    # A lane idle with a request of its own waiting starts: on a request, and whenever a group
    # ends, since the other lane may have been waiting on its logs.
    def _started(self) -> None:
        for lane in LANES:
            task = self._tasks.get(lane)
            idle = task is None or task.done()
            if idle and any(queued.lane == lane for queued in self._queue):
                self._tasks[lane] = asyncio.create_task(self._lane(lane))

    # No timer: what arrives while a transaction is out is the next group. A lane ends when it has
    # nothing it may write; the other lane starts it again when it lets a log go.
    async def _lane(self, lane: Lane) -> None:
        while group := self._taken(lane):
            logs = {queued.append.log for queued in group}
            self._writing |= logs
            try:
                await self._settled(group)
            finally:
                self._writing -= logs
                self._started()

    # A log is in a group once, and never while the other lane writes it or holds an earlier request
    # of it: its later requests, and every one past the group's size, wait in the order they came.
    # A request its caller gave up on is not written.
    def _taken(self, lane: Lane) -> list[_Queued]:
        group: list[_Queued] = []
        later: deque[_Queued] = deque()
        logs = set(self._writing)
        size = 0
        while self._queue:
            queued = self._queue.popleft()
            if queued.answer.done():
                continue
            entries = len(queued.append.entries)
            fits = not group or size + entries <= MOST_IN_A_GROUP
            if queued.lane == lane and queued.append.log not in logs and fits:
                group.append(queued)
                size += entries
            else:
                later.append(queued)
            logs.add(queued.append.log)
        self._queue = later
        return group

    # A member whose own entries break the transaction (a constraint, bytes Postgres refuses) would
    # take the group down with it, so the group is written again a member at a time and only that
    # one is refused. Anything else (the pool, the network) is every member's answer.
    async def _settled(self, group: list[_Queued]) -> None:
        try:
            outcomes = await _transaction(self.pool, [queued.append for queued in group])
        except (psycopg.errors.IntegrityError, psycopg.errors.DataError):
            logger.warning("a group of %d broke; each is written alone", len(group), exc_info=True)
            for queued in group:
                await self._alone(queued)
            return
        except Exception as broke:
            logger.warning("a group of %d was not written", len(group), exc_info=True)
            for queued in group:
                _answered(queued.answer, broke)
            return
        for queued, outcome in zip(group, outcomes, strict=True):
            _answered(queued.answer, outcome)

    async def _alone(self, queued: _Queued) -> None:
        try:
            [outcome] = await _transaction(self.pool, [queued.append])
        except Exception as broke:
            logger.warning("an append to %s was refused", queued.append.log, exc_info=True)
            _answered(queued.answer, broke)
            return
        _answered(queued.answer, outcome)


LANES: tuple[Lane, ...] = ("fed", "plain")


# What a first-of-its-type request is answered with when its log did not need it.
NOT_NEEDED = Batch(entries=[], replayed=False)


# Every statement covers the whole group: the heads begun, locked, read and moved at once, the
# rows written at once, the facts folded at once. The answers are known before the commit and
# handed out only after it.
async def _transaction(pool: Pool, group: Sequence[Append]) -> list[Batch | Conflict]:
    begun = [
        append
        for append in group
        if append.kind == "entry" or (append.kind == "batch" and append.after == 0)
    ]
    async with pool.connection() as connection, connection.transaction():
        if any(_fed(append) for append in group):
            await connection.execute(FEED_ORDER, {"key": FEED_ORDER_KEY})
        heads = await _heads(connection, HEADS_LOCKED, {"logs": [append.log for append in group]})
        missing = [append for append in begun if append.log not in heads]
        if missing:
            heads |= await _heads(connection, HEADS_BEGUN, _begun(missing))
        typed = await _held_types(
            connection, [append for append in group if append.kind == "first"]
        )
        typed |= await _unanswerable(
            connection, [append for append in group if append.kind == "answer"]
        )
        outcomes = [_outcome_of(append, heads.get(append.log), typed) for append in group]
        moved = [
            (append, outcome)
            for append, outcome in zip(group, outcomes, strict=True)
            if isinstance(outcome, _Moved)
        ]
        numbered = [_numbered(append, first=outcome.first) for append, outcome in moved]
        entries = [entry for batch in numbered for entry in batch]
        if moved:
            await connection.execute(WRITTEN, {**_heads_moved(moved), **_rows(entries)})
        await record(connection, entries)
    taken = iter(numbered)
    return [
        Batch(entries=next(taken), replayed=False) if isinstance(outcome, _Moved) else outcome
        for outcome in outcomes
    ]


async def _held_types(connection: Connection, firsts: Sequence[Append]) -> set[str]:
    if not firsts:
        return set()
    wanted = {
        "logs": [append.log for append in firsts],
        "types": [append.entries[0].type for append in firsts],
    }
    rows = await (await connection.execute(HELD_TYPES, wanted)).fetchall()
    return {str(row["log"]) for row in rows}


async def _unanswerable(connection: Connection, answers: Sequence[Append]) -> set[str]:
    if not answers:
        return set()
    wanted = {
        "logs": [append.log for append in answers],
        "ids": [str(append.entries[0].data.get("call_id")) for append in answers],
    }
    rows = await (await connection.execute(UNANSWERABLE, wanted)).fetchall()
    return {str(row["log"]) for row in rows}


def _fed(append: Append) -> bool:
    return any(not item.ephemeral and item.type in FED_TYPES for item in append.entries)


async def _heads(
    connection: Connection, statement: LiteralString, params: Mapping[str, object]
) -> dict[str, DictRow]:
    rows = await (await connection.execute(statement, params)).fetchall()
    return {str(row["log"]): row for row in rows}


def _begun(missing: Sequence[Append]) -> dict[str, object]:
    return {
        "logs": [append.log for append in missing],
        "agents": [append.agent for append in missing],
        "calls": [append.call for append in missing],
    }


# A first-of-its-type request is not needed on a log never written, sealed, or holding its type;
# an answer, on one that never asked its call id or holds its answer already.
def _outcome_of(append: Append, head: DictRow | None, typed: set[str]) -> _Moved | Batch | Conflict:
    guarded = append.kind in {"first", "answer"}
    if guarded and (append.log in typed or head is None or head["sealed"]):
        return NOT_NEEDED
    return _outcome(append, head)


def _outcome(append: Append, head: DictRow | None) -> _Moved | Batch | Conflict:
    if head is None and append.kind == "score":
        return Conflict(f"call {append.call} has no log to judge")
    if append.kind == "entry" and (head is None or head["sealed"]):
        kind = append.entries[0].type
        return Conflict(f"call {append.call} has ended: {kind} cannot be appended")
    if head is not None and append.kind != "batch":
        seq = int(head["seq"]) + 1
        return _Moved(
            first=seq, seq=seq, written=int(head["written"]), written_seq=head["written_seq"]
        )
    n = len(append.entries)
    if head is not None and not head["sealed"] and int(head["written"]) == append.after:
        seq = int(head["seq"]) + n
        return _Moved(first=seq - n + 1, seq=seq, written=append.after + n, written_seq=seq)
    first = _replayed_from(append, head)
    if isinstance(first, Conflict):
        return first
    return Batch(entries=_numbered(append, first=first), replayed=True)


# A retry of a batch the log took is answered even after the seal, so the replay is asked first.
def _replayed_from(append: Append, head: DictRow | None) -> int | Conflict:
    n, after = len(append.entries), append.after
    written = 0 if head is None else int(head["written"])
    last = None if head is None else head["written_seq"]
    if after + n == written and last is not None:
        return int(last) - n + 1
    if head is not None and head["sealed"]:
        return Conflict(f"call {append.call} has ended: its batch cannot be appended")
    return Conflict(
        f"call {append.call}: the log took {written} entries from its worker, "
        f"and the batch says {after} before its {n}"
    )


def _numbered(append: Append, *, first: int) -> list[Entry]:
    return [
        Entry(
            seq=seq,
            ts=item.ts,
            call=append.call,
            agent=append.agent,
            type=item.type,
            ephemeral=item.ephemeral,
            data=item.data,
        )
        for seq, item in enumerate(append.entries, first)
    ]


# A verdict on a sealed log names nothing of the log: it only takes the next seq.
def _heads_moved(moved: Sequence[tuple[Append, _Moved]]) -> dict[str, object]:
    return {
        "logs": [append.log for append, _ in moved],
        "seqs": [outcome.seq for _, outcome in moved],
        "written": [outcome.written for _, outcome in moved],
        "written_seqs": [outcome.written_seq for _, outcome in moved],
        "agents": [None if append.kind == "score" else append.agent for append, _ in moved],
        "heads_calls": [None if append.kind == "score" else append.call for append, _ in moved],
        "heads_stamps": [
            None if append.kind == "score" else append.entries[0].ts for append, _ in moved
        ],
    }


def _rows(entries: Sequence[Entry]) -> dict[str, object]:
    kept = [entry for entry in entries if not entry.ephemeral]
    return {
        "calls": [entry.call for entry in kept],
        "row_seqs": [entry.seq for entry in kept],
        "stamps": [entry.ts for entry in kept],
        "row_agents": [entry.agent for entry in kept],
        "types": [entry.type for entry in kept],
        "data": [Jsonb(entry.data) for entry in kept],
    }


# A caller that gave up has a cancelled future: its entries were written all the same.
def _answered(answer: asyncio.Future[Batch], outcome: Batch | Exception) -> None:
    if answer.done():
        return
    if isinstance(outcome, Exception):
        answer.set_exception(outcome)
        return
    answer.set_result(outcome)

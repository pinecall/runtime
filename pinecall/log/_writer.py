"""The writer: every log's appends gathered for a few milliseconds, written in one transaction."""

import asyncio
import logging
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

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

# The longest a request waits for its group to close, and the most entries one group carries.
GATHER_S = 0.005
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

# A head at seq 0 for each log that may begin in this group; the update below moves it as any other.
HEADS_BEGUN = """
insert into call_log_head (log, agent, call)
select begun.log, begun.agent, begun.call
from unnest(%(logs)s::text[], %(agents)s::text[], %(calls)s::text[]) as begun(log, agent, call)
order by begun.log
on conflict (log) do nothing
"""

# Locked in the order of their names, so two writers never wait on each other's rows in a circle.
HEADS_LOCKED = """
select log, seq, sealed, written, written_seq from call_log_head
where log = any(%(logs)s)
order by log
for update
"""

HEADS_MOVED = """
update call_log_head as head
set seq         = moved.seq,
    written     = moved.written,
    written_seq = moved.written_seq,
    agent       = coalesce(head.agent, moved.agent),
    call        = coalesce(head.call, moved.call),
    started_at  = coalesce(head.started_at, moved.started_at)
from unnest(%(logs)s::text[], %(seqs)s::bigint[], %(written)s::bigint[],
            %(written_seqs)s::bigint[], %(agents)s::text[], %(calls)s::text[],
            %(stamps)s::float8[])
     as moved(log, seq, written, written_seq, agent, call, started_at)
where head.log = moved.log
"""

ROWS_WRITTEN = """
insert into call_log (call, seq, ts, agent, type, ephemeral, data)
select kept.call, kept.seq, kept.ts, kept.agent, kept.type, false, kept.data
from unnest(%(calls)s::text[], %(seqs)s::bigint[], %(stamps)s::float8[], %(agents)s::text[],
            %(types)s::text[], %(data)s::jsonb[])
     as kept(call, seq, ts, agent, type, data)
"""

# The gateway's own entry, a worker's batch, or a verdict written again on a sealed log.
type Kind = Literal["entry", "batch", "score"]


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
    """A request waiting for its group, when it came, and where its answer goes."""

    append: Append
    at: float
    answer: asyncio.Future[Batch]


@dataclass(frozen=True, slots=True)
class _Moved:
    """A request the log takes: its head as it will be, and the first seq of its entries."""

    first: int
    seq: int
    written: int
    written_seq: int | None


class Writer:
    """The appends not yet written, and the task that writes them a group at a time."""

    def __init__(self, pool: Pool) -> None:
        """Keep the pool; the task starts with the first request and ends when none is left."""
        self.pool = pool
        self._queue: deque[_Queued] = deque()
        self._queued = 0
        self._arrived = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def written(self, append: Append) -> Batch:
        """Queue the request; its entries numbered, once the transaction that wrote them commits."""
        loop = asyncio.get_running_loop()
        answer: asyncio.Future[Batch] = loop.create_future()
        self._queue.append(_Queued(append, loop.time(), answer))
        self._queued += len(append.entries)
        self._arrived.set()
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._writing())
        return await answer

    async def drained(self) -> None:
        """Return once every request queued so far is written and answered."""
        while self._task is not None and not self._task.done():
            await asyncio.shield(self._task)

    # One transaction at a time: a log's next group waits for its last one's commit, so its seqs
    # are given in the order its requests came.
    async def _writing(self) -> None:
        while self._queue:
            await self._gathered()
            group = self._taken()
            if group:
                await self._settled(group)

    async def _gathered(self) -> None:
        loop = asyncio.get_running_loop()
        closes = self._queue[0].at + GATHER_S
        while self._queued < MOST_IN_A_GROUP and (left := closes - loop.time()) > 0:
            self._arrived.clear()
            try:
                async with asyncio.timeout(left):
                    await self._arrived.wait()
            except TimeoutError:
                return

    # A log is in a group once: its later requests, and every one past the group's size, wait for
    # the next group in the order they came. A request its caller gave up on is not written.
    def _taken(self) -> list[_Queued]:
        group: list[_Queued] = []
        later: deque[_Queued] = deque()
        logs: set[str] = set()
        size = 0
        while self._queue:
            queued = self._queue.popleft()
            entries = len(queued.append.entries)
            if queued.answer.done():
                self._queued -= entries
                continue
            fits = not group or size + entries <= MOST_IN_A_GROUP
            if queued.append.log not in logs and fits:
                group.append(queued)
                size += entries
            else:
                later.append(queued)
            logs.add(queued.append.log)
        self._queue = later
        self._queued -= size
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
        if begun:
            await connection.execute(
                HEADS_BEGUN,
                {
                    "logs": [append.log for append in begun],
                    "agents": [append.agent for append in begun],
                    "calls": [append.call for append in begun],
                },
            )
        heads = await _heads(connection, [append.log for append in group])
        outcomes = [_outcome(append, heads.get(append.log)) for append in group]
        moved = [
            (append, outcome)
            for append, outcome in zip(group, outcomes, strict=True)
            if isinstance(outcome, _Moved)
        ]
        numbered = [_numbered(append, first=outcome.first) for append, outcome in moved]
        entries = [entry for batch in numbered for entry in batch]
        await _heads_moved(connection, moved)
        await _rows_written(connection, entries)
        await record(connection, entries)
    taken = iter(numbered)
    return [
        Batch(entries=next(taken), replayed=False) if isinstance(outcome, _Moved) else outcome
        for outcome in outcomes
    ]


def _fed(append: Append) -> bool:
    return any(not item.ephemeral and item.type in FED_TYPES for item in append.entries)


async def _heads(connection: Connection, logs: Sequence[str]) -> dict[str, DictRow]:
    rows = await (await connection.execute(HEADS_LOCKED, {"logs": list(logs)})).fetchall()
    return {str(row["log"]): row for row in rows}


# What the head's locked row says of the request: taken under the next seqs, a batch the log took
# before (answered with the seqs it was given, even after the seal), or refused in a sentence.
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
async def _heads_moved(connection: Connection, moved: Sequence[tuple[Append, _Moved]]) -> None:
    if not moved:
        return
    await connection.execute(
        HEADS_MOVED,
        {
            "logs": [append.log for append, _ in moved],
            "seqs": [outcome.seq for _, outcome in moved],
            "written": [outcome.written for _, outcome in moved],
            "written_seqs": [outcome.written_seq for _, outcome in moved],
            "agents": [None if append.kind == "score" else append.agent for append, _ in moved],
            "calls": [None if append.kind == "score" else append.call for append, _ in moved],
            "stamps": [
                None if append.kind == "score" else append.entries[0].ts for append, _ in moved
            ],
        },
    )


async def _rows_written(connection: Connection, entries: Sequence[Entry]) -> None:
    kept = [entry for entry in entries if not entry.ephemeral]
    if not kept:
        return
    await connection.execute(
        ROWS_WRITTEN,
        {
            "calls": [entry.call for entry in kept],
            "seqs": [entry.seq for entry in kept],
            "stamps": [entry.ts for entry in kept],
            "agents": [entry.agent for entry in kept],
            "types": [entry.type for entry in kept],
            "data": [Jsonb(entry.data) for entry in kept],
        },
    )


# A caller that gave up has a cancelled future: its entries were written all the same.
def _answered(answer: asyncio.Future[Batch], outcome: Batch | Exception) -> None:
    if answer.done():
        return
    if isinstance(outcome, Exception):
        answer.set_exception(outcome)
        return
    answer.set_result(outcome)

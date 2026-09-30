"""The log in Postgres: a call's or an agent's entries, the seq each one is born with, its owner."""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from pinecall.domain.agent import Versions
from pinecall.domain.errors import Conflict, DeclarationRefused
from pinecall.domain.names import Env, JsonObject, parse_env
from pinecall.domain.scope import Scope
from pinecall.log.facts import record
from pinecall.log.reduce import Metered
from pinecall.postgres.pool import Connection, Pool
from pinecall.wire.frames import Entry

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 500

# An agent's own log is named "@<agent>", a name the CHECK on call_log keeps from any call id.
AGENT_LOG_PREFIX = "@"

# The head row's lock puts concurrent appends in order. A sealed log matches no row, so the
# statement returns nothing: that is the refusal. An ephemeral entry takes a seq and writes no row.
APPEND = """
with numbered as (
    insert into call_log_head as head (log, agent, call, seq, started_at)
    values (%(log)s, %(agent)s, %(call)s, 1, %(ts)s)
    on conflict (log) do update
        set seq        = head.seq + 1,
            agent      = coalesce(head.agent, excluded.agent),
            call       = coalesce(head.call, excluded.call),
            started_at = coalesce(head.started_at, excluded.started_at)
        where not head.sealed
    returning seq
), written as (
    insert into call_log (call, seq, ts, agent, type, ephemeral, data)
    select %(call)s, numbered.seq, %(ts)s, %(agent)s, %(type)s, %(ephemeral)s, %(data)s
    from numbered
    where not %(ephemeral)s
)
select seq from numbered
"""

# The most entries a worker's batch may carry.
MOST_IN_A_BATCH = 256

# One statement, as APPEND: the head moves n seqs only when the log is open and has taken `after`
# entries from its worker, and the durable entries are inserted from the seq it returns. A log
# nothing was written to is proposed only for a first batch; one the guard refuses returns no row.
# On a conflict the right-hand side reads the row as it was, so written_seq is the last of the n.
APPEND_MANY = """
with moved as (
    insert into call_log_head as head (log, agent, call, seq, written, written_seq, started_at)
    select %(log)s, %(agent)s, %(call)s, %(n)s, %(n)s, %(n)s, %(started_at)s
    where %(after)s = 0 or exists (select 1 from call_log_head where log = %(log)s)
    on conflict (log) do update
        set seq         = head.seq + %(n)s,
            written     = head.written + %(n)s,
            written_seq = head.seq + %(n)s,
            agent       = coalesce(head.agent, excluded.agent),
            call        = coalesce(head.call, excluded.call),
            started_at  = coalesce(head.started_at, excluded.started_at)
        where not head.sealed and head.written = %(after)s
    returning seq
), kept as (
    insert into call_log (call, seq, ts, agent, type, ephemeral, data)
    select %(call)s, moved.seq - %(n)s + durable.place, durable.ts, %(agent)s, durable.type,
           false, durable.data
    from moved,
         unnest(%(places)s::bigint[], %(stamps)s::float8[], %(types)s::text[], %(data)s::jsonb[])
             as durable(place, ts, type, data)
)
select seq from moved
"""

# The one write a sealed log takes: the same lock, no sealed check.
RESCORED = """
with numbered as (
    update call_log_head set seq = seq + 1 where log = %(log)s returning seq
), written as (
    insert into call_log (call, seq, ts, agent, type, ephemeral, data)
    select %(log)s, numbered.seq, %(ts)s, %(agent)s, 'call.score', false, %(data)s
    from numbered
)
select seq from numbered
"""

PAGE = """
select call, seq, ts, agent, type, ephemeral, data
from call_log
where log = %(log)s and seq > %(after)s
order by seq
limit %(limit)s
"""

# SealCallRequest a log nothing was written to is allowed: recovery may seal before the first entry.
SEAL = """
insert into call_log_head (log, call, sealed) values (%(call)s, %(call)s, true)
on conflict (log) do update set sealed = true
"""

HEAD = "select seq, sealed, written, written_seq from call_log_head where log = %(log)s"

# Taken before the look, so a second writer waits for the first to commit and then sees its entry.
HEAD_HELD = "select 1 from call_log_head where log = %(log)s and not sealed for update"

OF_ITS_TYPE = "select 1 from call_log where log = %(log)s and type = %(type)s limit 1"


# The first claim wins, and it may come before the first entry.
CLAIM = """
insert into call_log_head as head
    (log, agent, call, org, env, holder, config_version, lexicon_version)
values (%(log)s, %(agent)s, %(call)s, %(org)s, %(env)s, %(holder)s, %(config)s, %(lexicon)s)
on conflict (log) do update
    set org             = coalesce(head.org, excluded.org),
        env             = coalesce(head.env, excluded.env),
        holder          = coalesce(head.holder, excluded.holder),
        config_version  = coalesce(head.config_version, excluded.config_version),
        lexicon_version = coalesce(head.lexicon_version, excluded.lexicon_version)
"""

OWNER = "select org from call_log_head where log = %(log)s"

CLAIMANT = "select org, env from call_log_head where log = %(log)s"

MOVED = """
with moved as (update call_log_head set org = %(org)s where agent = %(agent)s returning log)
select count(*) as moved from moved
"""

# Served by call_log_metered (type, position).
ACROSS = """
select entry.position, head.org, entry.call, entry.seq, entry.ts, entry.agent, entry.type,
       entry.ephemeral, entry.data
from call_log entry
join call_log_head head on head.log = entry.log
where entry.type = any(%(types)s) and entry.position > %(after)s
order by entry.position
limit %(limit)s
"""

NEWEST_CALLS = """
select call from call_log_head
where call is not null and (%(agent)s::text is null or agent = %(agent)s)
order by started_at desc nulls last, log desc
limit %(limit)s
"""

NEWEST_LIVE_CALL = """
select call from call_log_head
where call is not null and not sealed
order by started_at desc nulls last, log desc
limit 1
"""


@dataclass(frozen=True, slots=True)
class Claimant:
    """Whose a log is and the world it was claimed in; no world on an agent's own log."""

    org: str
    env: Env | None


@dataclass(frozen=True, slots=True)
class Claim:
    """What a call log is claimed with: its scope and the versions it was built on."""

    scope: Scope
    versions: Versions = field(default_factory=Versions)


@dataclass(frozen=True, slots=True)
class Unnumbered:
    """An entry the log has not numbered yet: what it is, whether a store keeps it, and when."""

    type: str
    data: JsonObject
    ephemeral: bool
    ts: float


@dataclass(frozen=True, slots=True)
class Batch:
    """A worker's batch as the log numbered it, and whether the log had taken it before."""

    entries: list[Entry]
    replayed: bool


class Store:
    """The log tables on a pool the store is given and never closes."""

    def __init__(self, pool: Pool, *, clock: Callable[[], float] = time.time) -> None:
        """Keep the pool and the clock the entries are stamped with."""
        self.pool = pool
        # ts is the runtime's clock, when it saw the event, never the database's.
        self.clock = clock

    async def append(
        self, call: str | None, agent: str, kind: str, data: JsonObject, *, ephemeral: bool
    ) -> Entry:
        """Write the entry with the next seq of its log, folding the call's facts with it."""
        entry = Entry(
            seq=0,
            ts=self.clock(),
            call=call,
            agent=agent,
            type=kind,
            ephemeral=ephemeral,
            data=data,
        )
        async with self.pool.connection() as connection, connection.transaction():
            await _appended(connection, entry)
        return entry

    # The look is a statement of its own after the lock: read committed gives it a fresh snapshot.
    async def append_first(
        self, call: str, agent: str, kind: str, data: JsonObject
    ) -> Entry | None:
        """Write a durable entry unless the call's log holds one of its type or is sealed."""
        entry = Entry(
            seq=0, ts=self.clock(), call=call, agent=agent, type=kind, ephemeral=False, data=data
        )
        async with self.pool.connection() as connection, connection.transaction():
            if await (await connection.execute(HEAD_HELD, {"log": call})).fetchone() is None:
                return None
            wanted = {"log": call, "type": kind}
            if await (await connection.execute(OF_ITS_TYPE, wanted)).fetchone() is not None:
                return None
            await _appended(connection, entry)
        return entry

    # One writer, in order: `after` is how many entries the log took from it before this batch.
    async def append_many(
        self, call: str | None, agent: str, entries: Sequence[Unnumbered], *, after: int
    ) -> Batch:
        """Write a worker's batch once under the next contiguous seqs, or answer a retry of it."""
        if not entries or len(entries) > MOST_IN_A_BATCH:
            raise DeclarationRefused(
                f"a batch carries 1 to {MOST_IN_A_BATCH} entries, not {len(entries)}"
            )
        log, n = log_name(call, agent), len(entries)
        stamps = _stamps(entries, self.clock())
        durable = [place for place, item in enumerate(entries, 1) if not item.ephemeral]
        moved = {
            "log": log,
            "agent": agent,
            "call": call,
            "n": n,
            "after": after,
            "started_at": stamps[0],
            "places": durable,
            "stamps": [stamps[place - 1] for place in durable],
            "types": [entries[place - 1].type for place in durable],
            "data": [Jsonb(entries[place - 1].data) for place in durable],
        }
        async with self.pool.connection() as connection, connection.transaction():
            row = await (await connection.execute(APPEND_MANY, moved)).fetchone()
            if row is None:
                head = await (await connection.execute(HEAD, {"log": log})).fetchone()
                first = _replayed_from(call, head, n=n, after=after)
                numbered = _numbered(call, agent, entries, first=first, stamps=stamps)
                return Batch(entries=numbered, replayed=True)
            numbered = _numbered(call, agent, entries, first=int(row["seq"]) - n + 1, stamps=stamps)
            await record(connection, numbered)
        return Batch(entries=numbered, replayed=False)

    async def rescored(self, call: str, agent: str, data: JsonObject) -> Entry:
        """Write a call.score to a sealed log, the one entry a sealed log still takes."""
        entry = Entry(
            seq=0,
            ts=self.clock(),
            call=call,
            agent=agent,
            type="call.score",
            ephemeral=False,
            data=data,
        )
        written = {"log": call, "agent": agent, "ts": entry.ts, "data": Jsonb(data)}
        async with self.pool.connection() as connection, connection.transaction():
            row = await (await connection.execute(RESCORED, written)).fetchone()
            if row is None:
                raise Conflict(f"call {call} has no log to judge")
            entry.seq = int(row["seq"])
            await record(connection, [entry])
        return entry

    async def since(self, log: str, *, after: int = 0, limit: int = DEFAULT_LIMIT) -> list[Entry]:
        """Return one page of the log's durable entries above the cursor, in seq order."""
        async with self.pool.connection() as connection:
            cursor = await connection.execute(PAGE, {"log": log, "after": after, "limit": limit})
            return [entry_of(row) for row in await cursor.fetchall()]

    async def whole(self, log: str, *, after: int = 0) -> list[Entry]:
        """Return every durable entry of the log above the cursor, oldest first."""
        found: list[Entry] = []
        while True:
            page = await self.since(log, after=after, limit=DEFAULT_LIMIT)
            found += page
            if len(page) < DEFAULT_LIMIT:
                return found
            after = page[-1].seq

    async def latest_seq(self, log: str) -> int:
        """Return the last seq the log gave out, ephemerals counted; 0 for a log never written."""
        head = await self._head(log)
        return 0 if head is None else int(head["seq"])

    async def written(self, log: str) -> int:
        """Return how many entries the log took from its writer's batches; 0 for none."""
        head = await self._head(log)
        return 0 if head is None else int(head["written"])

    async def sealed(self, log: str) -> bool:
        """Return whether the log is sealed."""
        head = await self._head(log)
        return head is not None and bool(head["sealed"])

    async def seal(self, call: str) -> None:
        """Seal the call's log; sealing twice changes nothing."""
        async with self.pool.connection() as connection:
            await connection.execute(SEAL, {"call": call})

    async def claim(
        self, call: str | None, agent: str, org: str, claim: Claim | None = None
    ) -> None:
        """Record the log's org, and for a call its scope and versions; the first claim stands."""
        scope = None if call is None or claim is None else claim.scope
        versions = Versions() if call is None or claim is None else claim.versions
        async with self.pool.connection() as connection:
            await connection.execute(
                CLAIM,
                {
                    "log": log_name(call, agent),
                    "agent": agent,
                    "call": call,
                    "org": org,
                    "env": None if scope is None else scope.env,
                    "holder": None if scope is None else scope.holder,
                    "config": versions.config,
                    "lexicon": versions.lexicon,
                },
            )

    async def owner(self, agent: str) -> str | None:
        """Return the org the agent's own log belongs to, or None while nobody claimed it."""
        async with self.pool.connection() as connection:
            row = await (await connection.execute(OWNER, {"log": log_name(None, agent)})).fetchone()
        return None if row is None or row["org"] is None else str(row["org"])

    async def claimant(self, call: str | None, agent: str) -> Claimant | None:
        """Return whose the log is and in which world, or None while nobody claimed it."""
        async with self.pool.connection() as connection:
            row = await (
                await connection.execute(CLAIMANT, {"log": log_name(call, agent)})
            ).fetchone()
        if row is None or row["org"] is None:
            return None
        env = None if row["env"] is None else parse_env(str(row["env"]))
        return Claimant(org=str(row["org"]), env=env)

    # The operator's way back from a claim: an agent registered with the wrong key.
    async def moved(self, agent: str, org: str) -> int:
        """Move every log of the agent to another org and return how many moved."""
        async with self.pool.connection() as connection:
            row = await (await connection.execute(MOVED, {"agent": agent, "org": org})).fetchone()
        return 0 if row is None else int(row["moved"])

    async def across(
        self, types: Sequence[str], *, after: int = 0, limit: int = DEFAULT_LIMIT
    ) -> list[Metered]:
        """Return one page of the metered entries of every log, by position, with their owners."""
        params = {"types": list(types), "after": after, "limit": limit}
        async with self.pool.connection() as connection:
            rows = await (await connection.execute(ACROSS, params)).fetchall()
        return [
            Metered(
                position=int(row["position"]),
                org=None if row["org"] is None else str(row["org"]),
                entry=entry_of(row),
            )
            for row in rows
        ]

    async def newest_calls(self, limit: int, *, agent: str | None = None) -> list[str]:
        """Return the newest calls of the box, or of one agent."""
        async with self.pool.connection() as connection:
            cursor = await connection.execute(NEWEST_CALLS, {"limit": limit, "agent": agent})
            return [str(row["call"]) for row in await cursor.fetchall()]

    async def newest_live_call(self) -> str | None:
        """Return the newest call whose log is not sealed, or None."""
        async with self.pool.connection() as connection:
            row = await (await connection.execute(NEWEST_LIVE_CALL)).fetchone()
        return None if row is None else str(row["call"])

    async def _head(self, log: str) -> DictRow | None:
        async with self.pool.connection() as connection:
            return await (await connection.execute(HEAD, {"log": log})).fetchone()


def log_name(call: str | None, agent: str) -> str:
    """Return the name a log goes by: the call id, or the agent's with the prefix."""
    return f"{AGENT_LOG_PREFIX}{agent}" if call is None else call


def entry_of(row: DictRow) -> Entry:
    """Return the entry a call_log row holds."""
    return Entry.model_validate(
        {name: row[name] for name in ("call", "seq", "ts", "agent", "type", "ephemeral", "data")}
    )


async def _appended(connection: Connection, entry: Entry) -> None:
    written = {
        **entry.written(),
        "log": log_name(entry.call, entry.agent),
        "data": Jsonb(entry.data),
    }
    row = await (await connection.execute(APPEND, written)).fetchone()
    if row is None:
        raise Conflict(f"call {entry.call} has ended: {entry.type} cannot be appended")
    entry.seq = int(row["seq"])
    await record(connection, [entry])


def _numbered(
    call: str | None,
    agent: str,
    entries: Sequence[Unnumbered],
    *,
    first: int,
    stamps: Sequence[float],
) -> list[Entry]:
    return [
        Entry(
            seq=seq,
            ts=ts,
            call=call,
            agent=agent,
            type=item.type,
            ephemeral=item.ephemeral,
            data=item.data,
        )
        for seq, item, ts in zip(range(first, first + len(entries)), entries, stamps, strict=True)
    ]


# The worker's clock says when each thing happened; the gateway's bounds it, and a batch never
# steps back inside itself.
def _stamps(entries: Sequence[Unnumbered], now: float) -> list[float]:
    stamps: list[float] = []
    for item in entries:
        stamp = min(item.ts, now)
        stamps.append(stamp if not stamps else max(stamp, stamps[-1]))
    return stamps


# A retry of a batch the log took is answered even after the seal, so the replay is asked first.
def _replayed_from(call: str | None, head: DictRow | None, *, n: int, after: int) -> int:
    written = 0 if head is None else int(head["written"])
    last = None if head is None else head["written_seq"]
    if after + n == written and last is not None:
        return int(last) - n + 1
    if head is not None and head["sealed"]:
        raise Conflict(f"call {call} has ended: its batch cannot be appended")
    raise Conflict(
        f"call {call}: the log took {written} entries from its worker, "
        f"and the batch says {after} before its {n}"
    )

"""The log in Postgres: a call's or an agent's entries, the seq each one is born with, its owner."""

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from psycopg.rows import DictRow
from psycopg.types.json import Jsonb

from pinecall.domain.agent import Versions
from pinecall.domain.errors import Conflict
from pinecall.domain.names import Env, JsonObject, parse_env
from pinecall.domain.scope import Scope
from pinecall.log.facts import record
from pinecall.log.reduce import Metered
from pinecall.postgres.pool import Pool
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

HEAD = "select seq, sealed from call_log_head where log = %(log)s"

# Head rows, so a call with only ephemeral entries is listed too.
LIST_CALLS = """
select call from call_log_head
where agent = %(agent)s and call is not null
order by started_at nulls last, log
"""

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

WORLD = "select env from call_log_head where log = %(log)s"

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
class Claim:
    """What a call log is claimed with: its scope and the versions it was built on."""

    scope: Scope
    versions: Versions = field(default_factory=Versions)


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
        written = {**entry.written(), "log": log_name(call, agent), "data": Jsonb(data)}
        async with self.pool.connection() as connection, connection.transaction():
            row = await (await connection.execute(APPEND, written)).fetchone()
            if row is None:
                raise Conflict(f"call {call} has ended: {kind} cannot be appended")
            entry.seq = int(row["seq"])
            await record(connection, entry)
        return entry

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
            await record(connection, entry)
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

    async def sealed(self, log: str) -> bool:
        """Return whether the log is sealed."""
        head = await self._head(log)
        return head is not None and bool(head["sealed"])

    async def seal(self, call: str) -> None:
        """Seal the call's log; sealing twice changes nothing."""
        async with self.pool.connection() as connection:
            await connection.execute(SEAL, {"call": call})

    async def list_calls(self, agent: str) -> list[str]:
        """Return every call the agent handled, oldest first."""
        async with self.pool.connection() as connection:
            rows = await (await connection.execute(LIST_CALLS, {"agent": agent})).fetchall()
        return [str(row["call"]) for row in rows]

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

    async def owner(self, call: str | None, agent: str) -> str | None:
        """Return the org the log belongs to, or None while nobody claimed it."""
        async with self.pool.connection() as connection:
            row = await (await connection.execute(OWNER, {"log": log_name(call, agent)})).fetchone()
        return None if row is None or row["org"] is None else str(row["org"])

    async def world(self, call: str) -> Env | None:
        """Return the world a call's log was claimed in, or None while nobody claimed it."""
        async with self.pool.connection() as connection:
            row = await (await connection.execute(WORLD, {"log": call})).fetchone()
        return None if row is None or row["env"] is None else parse_env(str(row["env"]))

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

"""An agent's tuning and lexicon, versioned per scope, and what is current."""

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal, LiteralString

from psycopg import sql
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from pinecall.domain.agent import Lexicon, Tuning, Version, Versions
from pinecall.domain.errors import Conflict
from pinecall.domain.names import PRODUCTION, Json, JsonObject
from pinecall.domain.scope import THE_ORGS_OWN, Scope
from pinecall.postgres.pool import Connection, Pool
from pinecall.tenancy.canary import bucket_of

type VersionedTable = Literal["agent_config", "lexicon"]


MOVED = "this corner is at v{newest} now, not the version you read: read it again, then set again"


HISTORY_LIMIT = 20


TUNING: TypeAdapter[Tuning] = TypeAdapter(Tuning)


HISTORY = sql.SQL("""
SELECT holder, version, {value} AS value, author, note, set_at FROM {table}
WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND agent = %(agent)s
ORDER BY version DESC LIMIT %(limit)s
""")


# The version a call ran on is the holder's or, when it fell through, the org's own.
AT = sql.SQL("""
SELECT holder, version, {value} AS value, author, note, set_at FROM {table}
WHERE org = %(org)s AND env = %(env)s AND holder IN (%(holder)s, '') AND agent = %(agent)s
  AND version = %(version)s
ORDER BY holder DESC LIMIT 1
""")


NEWEST = sql.SQL("""
SELECT coalesce(max(version), 0) AS newest FROM {table}
WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND agent = %(agent)s
""")


# The next version, unless the scope moved since the writer read it: then no row.
PUT_TUNING = """
INSERT INTO agent_config (org, env, holder, agent, version, config, author, note)
SELECT %(org)s, %(env)s, %(holder)s, %(agent)s, coalesce(max(version), 0) + 1, %(value)s,
       %(author)s, %(note)s
FROM agent_config
WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND agent = %(agent)s
HAVING %(if_version)s::integer IS NULL OR coalesce(max(version), 0) = %(if_version)s
ON CONFLICT (org, env, holder, agent, version) DO NOTHING
RETURNING version
"""


PUT_LEXICON = """
INSERT INTO lexicon (org, env, holder, agent, version, said, heard, author, note)
SELECT %(org)s, %(env)s, %(holder)s, %(agent)s, coalesce(max(version), 0) + 1, %(said)s,
       %(heard)s, %(author)s, %(note)s
FROM lexicon
WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s AND agent = %(agent)s
HAVING %(if_version)s::integer IS NULL OR coalesce(max(version), 0) = %(if_version)s
ON CONFLICT (org, env, holder, agent, version) DO NOTHING
RETURNING version
"""


# Both chains in one statement, so the tuning and the lexicon a call is built on are read from
# the same moment: the holder's newest row, then the org's own, of each. Where a level stands on a
# canary (tenancy/canary.py), a call whose bucket is under its share runs the canary's version and
# every other call, or a read for no call, the newest version but that one.
STANDING = """
SELECT * FROM (
    SELECT DISTINCT ON (config.holder) 'tuning' AS kind, config.holder, config.version,
           config.config AS value, config.author, config.note, config.set_at
    FROM agent_config config
    LEFT JOIN LATERAL (
        SELECT canary.version, canary.share FROM agent_canaries canary
        WHERE canary.org = config.org AND canary.env = config.env
          AND canary.holder = config.holder AND canary.agent = config.agent
        ORDER BY canary.set_at DESC, canary.id DESC
        LIMIT 1
    ) standing ON true
    WHERE config.org = %(org)s AND config.env = %(env)s AND config.holder IN (%(holder)s, '')
      AND config.agent = %(agent)s
      AND (standing.version IS NULL
           OR (config.version = standing.version)
              = coalesce(%(bucket)s::integer < standing.share, false))
    ORDER BY config.holder DESC, config.version DESC
) tuning
UNION ALL
SELECT * FROM (
    SELECT DISTINCT ON (holder) 'lexicon', holder, version,
           jsonb_build_object('said', said, 'heard', heard), author, note, set_at
    FROM lexicon
    WHERE org = %(org)s AND env = %(env)s AND holder IN (%(holder)s, '') AND agent = %(agent)s
    ORDER BY holder DESC, version DESC
) lexicon
ORDER BY kind, holder DESC
"""


EVERY_AGENT = """
SELECT DISTINCT ON (agent, holder) agent, holder, version, config AS value, author, note, set_at
FROM agent_config
WHERE org = %(org)s AND env = %(env)s AND holder IN (%(holder)s, '')
ORDER BY agent, holder DESC, version DESC
"""


# The versions a drift names: the ones its calls ran and the ones set in its window. The same
# number at both levels is the holder's, as the version a call ran on is read (AT).
NOTED = """
SELECT DISTINCT ON (version) holder, version, author, note, set_at FROM agent_config
WHERE org = %(org)s AND env = %(env)s AND holder IN (%(holder)s, '') AND agent = %(agent)s
  AND (version = ANY(%(versions)s)
       OR (set_at >= to_timestamp(%(since)s) AND set_at < to_timestamp(%(until)s)))
ORDER BY version, holder DESC
"""


@dataclass(frozen=True)
class Noted:
    """A version of the agent's tuning without its value: whose, which, who wrote it, why, when."""

    holder: str
    version: int
    author: str
    note: str | None
    set_at: datetime


@dataclass(frozen=True)
class Written:
    """Who writes a version, why, and the version they read, when they want no other."""

    author: str
    note: str | None = None
    if_version: int | None = None


@dataclass(frozen=True)
class SideBySide[T]:
    """What a settings screen shows side by side: yours, the team's and production's newest."""

    yours: Version[T] | None
    team: Version[T] | None
    production: Version[T] | None


@dataclass(frozen=True)
class Current:
    """The tuning and the lexicon a call in the scope is built on, and their versions."""

    tuning: Tuning
    lexicon: Lexicon
    versions: Versions


# What each table keeps as its value, read as one JSON object so both are read the same way.
VALUE: dict[VersionedTable, sql.Composable] = {
    "agent_config": sql.SQL("config"),
    "lexicon": sql.SQL("jsonb_build_object('said', said, 'heard', heard)"),
}


async def tuning_history(
    pool: Pool, scope: Scope, agent: str, *, limit: int = HISTORY_LIMIT
) -> list[Version[Tuning]]:
    """The scope's own versions of the agent's tuning, newest first."""
    rows = await _history(pool, "agent_config", scope, agent, limit)
    return [_tuning(row) for row in rows]


async def lexicon_history(
    pool: Pool, scope: Scope, agent: str, *, limit: int = HISTORY_LIMIT
) -> list[Version[Lexicon]]:
    """The scope's own versions of the agent's lexicon, newest first."""
    return [_lexicon(row) for row in await _history(pool, "lexicon", scope, agent, limit)]


async def tuning_at(pool: Pool, scope: Scope, agent: str, version: int) -> Version[Tuning] | None:
    """One version of the agent's tuning, the scope's own or the org's it fell through to."""
    row = await _at(pool, "agent_config", scope, agent, version)
    return None if row is None else _tuning(row)


async def lexicon_at(pool: Pool, scope: Scope, agent: str, version: int) -> Version[Lexicon] | None:
    """One version of the agent's lexicon, the scope's own or the org's it fell through to."""
    row = await _at(pool, "lexicon", scope, agent, version)
    return None if row is None else _lexicon(row)


async def put_tuning(pool: Pool, scope: Scope, agent: str, tuning: Tuning, written: Written) -> int:
    """Write the agent's next tuning in the scope; a scope moved since it was read refuses."""
    values: dict[str, object] = {
        **_where(scope, agent),
        "value": Jsonb(_stored(tuning)),
        "author": written.author,
        "note": written.note,
        "if_version": written.if_version,
    }
    return await _put(pool, "agent_config", PUT_TUNING, values)


async def put_lexicon(
    pool: Pool, scope: Scope, agent: str, lexicon: Lexicon, written: Written
) -> int:
    """Write the agent's next lexicon in the scope; a scope moved since it was read refuses."""
    values: dict[str, object] = {
        **_where(scope, agent),
        "said": Jsonb(dict(lexicon.said)),
        "heard": Jsonb(list(lexicon.heard)),
        "author": written.author,
        "note": written.note,
        "if_version": written.if_version,
    }
    return await _put(pool, "lexicon", PUT_LEXICON, values)


# The call picks between a canary's version and the rest by its id; no call is the rest.
async def current(pool: Pool, scope: Scope, agent: str, *, call: str | None = None) -> Current:
    """What a call in the scope is built on: each knob from the nearest scope that sets it."""
    params = {**_where(scope, agent), "bucket": None if call is None else bucket_of(call)}
    async with pool.connection() as connection:
        rows = await (await connection.execute(STANDING, params)).fetchall()
    tuned = resolve([_tuning(row) for row in rows if row["kind"] == "tuning"])
    words = next((_lexicon(row) for row in rows if row["kind"] == "lexicon"), None)
    return Current(
        tuning=Tuning() if tuned is None else tuned.value,
        lexicon=Lexicon() if words is None else words.value,
        versions=Versions(
            config=None if tuned is None else tuned.version,
            lexicon=None if words is None else words.version,
        ),
    )


async def versions_noted(
    pool: Pool, scope: Scope, agent: str, versions: Collection[int], between: tuple[float, float]
) -> list[Noted]:
    """The agent's versions named, and those set in [since, until), oldest first, without values."""
    since, until = between
    params = {**_where(scope, agent), "versions": list(versions), "since": since, "until": until}
    async with pool.connection() as connection:
        rows = await (await connection.execute(NOTED, params)).fetchall()
    return [
        Noted(row["holder"], row["version"], row["author"], row["note"], row["set_at"])
        for row in rows
    ]


async def every_tuning(pool: Pool, scope: Scope) -> dict[str, Tuning]:
    """Every agent's tuning as it stands in the scope."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(EVERY_AGENT, _where(scope, None))).fetchall()
    chains: dict[str, list[Version[Tuning]]] = {}
    for row in rows:
        chains.setdefault(row["agent"], []).append(_tuning(row))
    resolved = {agent: resolve(chain) for agent, chain in chains.items()}
    return {agent: kept.value for agent, kept in resolved.items() if kept is not None}


async def tuning_side_by_side(pool: Pool, scope: Scope, agent: str) -> SideBySide[Tuning]:
    """The agent's tuning as yours, the team's and production's, none falling through."""
    return SideBySide(
        yours=await _newest_tuning(pool, scope, agent) if scope.holder else None,
        team=await _newest_tuning(pool, replace(scope, holder=THE_ORGS_OWN), agent),
        production=await _newest_tuning(pool, Scope(scope.org, PRODUCTION), agent),
    )


async def lexicon_side_by_side(pool: Pool, scope: Scope, agent: str) -> SideBySide[Lexicon]:
    """The agent's lexicon as yours, the team's and production's, none falling through."""
    return SideBySide(
        yours=await _newest_lexicon(pool, scope, agent) if scope.holder else None,
        team=await _newest_lexicon(pool, replace(scope, holder=THE_ORGS_OWN), agent),
        production=await _newest_lexicon(pool, Scope(scope.org, PRODUCTION), agent),
    )


# A knob is set when it is not None, so a falsy value is set and does not fall through:
# `bases: []` is "no bases", not "the team's". The version is the nearest row that set any.
def resolve(chain: Sequence[Version[Tuning]]) -> Version[Tuning] | None:
    """Merge a nearest-first chain knob by knob; None when no row sets any knob."""
    supplying = [(row, stored) for row in chain if (stored := _stored(row.value))]
    if not supplying:
        return None
    merged: JsonObject = {}
    for _row, stored in reversed(supplying):
        merged.update(stored)
    nearest, _ = supplying[0]
    return replace(nearest, value=TUNING.validate_python(merged))


async def _newest_tuning(pool: Pool, scope: Scope, agent: str) -> Version[Tuning] | None:
    rows = await _history(pool, "agent_config", scope, agent, 1)
    return _tuning(rows[0]) if rows else None


async def _newest_lexicon(pool: Pool, scope: Scope, agent: str) -> Version[Lexicon] | None:
    rows = await _history(pool, "lexicon", scope, agent, 1)
    return _lexicon(rows[0]) if rows else None


async def _history(
    pool: Pool, table: VersionedTable, scope: Scope, agent: str, limit: int
) -> list[DictRow]:
    query = HISTORY.format(**_parts(table))
    async with pool.connection() as connection:
        values = {**_where(scope, agent), "limit": limit}
        return await (await connection.execute(query, values)).fetchall()


async def _at(
    pool: Pool, table: VersionedTable, scope: Scope, agent: str, version: int
) -> DictRow | None:
    query = AT.format(**_parts(table))
    async with pool.connection() as connection:
        values = {**_where(scope, agent), "version": version}
        return await (await connection.execute(query, values)).fetchone()


async def _put(
    pool: Pool, table: VersionedTable, statement: LiteralString, values: dict[str, object]
) -> int:
    async with pool.connection() as connection:
        row = await (await connection.execute(statement, values)).fetchone()
        if row is not None:
            return int(row["version"])
        raise Conflict(MOVED.format(newest=await _newest(connection, table, values)))


async def _newest(connection: Connection, table: VersionedTable, values: dict[str, object]) -> int:
    row = await (await connection.execute(NEWEST.format(**_parts(table)), values)).fetchone()
    return 0 if row is None else int(row["newest"])


def _parts(table: VersionedTable) -> dict[str, sql.Composable]:
    return {"table": sql.Identifier(table), "value": VALUE[table]}


def _where(scope: Scope, agent: str | None) -> dict[str, object]:
    return {"org": scope.org, "env": scope.env, "holder": scope.holder, "agent": agent}


def _stored(tuning: Tuning) -> JsonObject:
    dumped: JsonObject = TUNING.dump_python(tuning, mode="json", exclude_none=True)
    return dumped


def _tuning(row: DictRow) -> Version[Tuning]:
    return _kept(row, TUNING.validate_python(row["value"]))


def _lexicon(row: DictRow) -> Version[Lexicon]:
    value: dict[str, Json] = row["value"]
    answer = value.get("said") or {}
    heard = value.get("heard") or []
    lexicon = Lexicon(
        said={str(word): str(spoken) for word, spoken in dict(answer).items()}
        if isinstance(answer, dict)
        else {},
        heard=tuple(str(word) for word in heard) if isinstance(heard, list) else (),
    )
    return _kept(row, lexicon)


def _kept[T](row: DictRow, value: T) -> Version[T]:
    return Version(
        holder=row["holder"],
        version=row["version"],
        author=row["author"],
        note=row["note"],
        set_at=row["set_at"],
        value=value,
    )

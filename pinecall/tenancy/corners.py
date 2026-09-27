"""Corners: an agent's tuning and the org's lexicon, versioned per corner, and what stands."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Literal, LiteralString

from psycopg import sql
from psycopg.rows import DictRow
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter

from pinecall.domain.errors import Conflict
from pinecall.domain.types import (
    PRODUCTION,
    THE_ORGS_OWN,
    Corner,
    Json,
    JsonObject,
    Kept,
    Lexicon,
    Tuning,
    Versions,
)
from pinecall.postgres.pool import Connection, Pool

MOVED = "this corner is at v{newest} now, not the version you read: read it again, then set again"
HISTORY_LIMIT = 20

TUNING: TypeAdapter[Tuning] = TypeAdapter(Tuning)

type Table = Literal["agent_config", "lexicon"]

# What each table keeps as its value, read as one JSON object so both are read the same way.
VALUE: dict[Table, sql.Composable] = {
    "agent_config": sql.SQL("config"),
    "lexicon": sql.SQL("jsonb_build_object('said', said, 'heard', heard)"),
}
# The tuning is an agent's; the lexicon is the corner's, over every agent.
OF_THE_AGENT: dict[Table, sql.Composable] = {
    "agent_config": sql.SQL("AND agent = %(agent)s"),
    "lexicon": sql.SQL(""),
}
HISTORY = sql.SQL("""
SELECT holder, version, {value} AS value, author, note, set_at FROM {table}
WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s {agent}
ORDER BY version DESC LIMIT %(limit)s
""")
# The version a call ran on is the holder's or, when it fell through, the org's own.
AT = sql.SQL("""
SELECT holder, version, {value} AS value, author, note, set_at FROM {table}
WHERE org = %(org)s AND env = %(env)s AND holder IN (%(holder)s, '') {agent}
  AND version = %(version)s
ORDER BY holder DESC LIMIT 1
""")
NEWEST = sql.SQL("""
SELECT coalesce(max(version), 0) AS newest FROM {table}
WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s {agent}
""")
# The next version, unless the corner moved since the writer read it: then no row.
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
INSERT INTO lexicon (org, env, holder, version, said, heard, author, note)
SELECT %(org)s, %(env)s, %(holder)s, coalesce(max(version), 0) + 1, %(said)s, %(heard)s,
       %(author)s, %(note)s
FROM lexicon
WHERE org = %(org)s AND env = %(env)s AND holder = %(holder)s
HAVING %(if_version)s::integer IS NULL OR coalesce(max(version), 0) = %(if_version)s
ON CONFLICT (org, env, holder, version) DO NOTHING
RETURNING version
"""
# Both chains in one statement, so the tuning and the lexicon a call is built on are read from
# the same moment: the holder's newest row, then the org's own, of each.
STANDING = """
SELECT * FROM (
    SELECT DISTINCT ON (holder) 'tuning' AS kind, holder, version, config AS value, author, note,
           set_at
    FROM agent_config
    WHERE org = %(org)s AND env = %(env)s AND holder IN (%(holder)s, '') AND agent = %(agent)s
    ORDER BY holder DESC, version DESC
) tuning
UNION ALL
SELECT * FROM (
    SELECT DISTINCT ON (holder) 'lexicon', holder, version,
           jsonb_build_object('said', said, 'heard', heard), author, note, set_at
    FROM lexicon
    WHERE org = %(org)s AND env = %(env)s AND holder IN (%(holder)s, '')
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


@dataclass(frozen=True)
class Written:
    """Who writes a version, why, and the version they read, when they want no other."""

    author: str
    note: str | None = None
    if_version: int | None = None


@dataclass(frozen=True)
class Corners[T]:
    """What a settings screen shows side by side: yours, the team's and production's newest."""

    yours: Kept[T] | None
    team: Kept[T] | None
    production: Kept[T] | None


@dataclass(frozen=True)
class Standing:
    """The tuning and the lexicon a call in the corner is built on, and their versions."""

    tuning: Tuning
    lexicon: Lexicon
    versions: Versions


async def tuning_history(
    pool: Pool, corner: Corner, agent: str, *, limit: int = HISTORY_LIMIT
) -> list[Kept[Tuning]]:
    """The corner's own versions of the agent's tuning, newest first."""
    rows = await _history(pool, "agent_config", corner, agent, limit)
    return [_tuning(row) for row in rows]


async def lexicon_history(
    pool: Pool, corner: Corner, *, limit: int = HISTORY_LIMIT
) -> list[Kept[Lexicon]]:
    """The corner's own versions of the lexicon, newest first."""
    return [_lexicon(row) for row in await _history(pool, "lexicon", corner, None, limit)]


async def tuning_at(pool: Pool, corner: Corner, agent: str, version: int) -> Kept[Tuning] | None:
    """One version of the agent's tuning, the corner's own or the org's it fell through to."""
    row = await _at(pool, "agent_config", corner, agent, version)
    return None if row is None else _tuning(row)


async def lexicon_at(pool: Pool, corner: Corner, version: int) -> Kept[Lexicon] | None:
    """One version of the lexicon, the corner's own or the org's it fell through to."""
    row = await _at(pool, "lexicon", corner, None, version)
    return None if row is None else _lexicon(row)


async def put_tuning(
    pool: Pool, corner: Corner, agent: str, tuning: Tuning, written: Written
) -> int:
    """Write the agent's next tuning in the corner; a corner moved since it was read refuses."""
    values: dict[str, object] = {
        **_where(corner, agent),
        "value": Jsonb(_stored(tuning)),
        "author": written.author,
        "note": written.note,
        "if_version": written.if_version,
    }
    return await _put(pool, "agent_config", PUT_TUNING, values)


async def put_lexicon(pool: Pool, corner: Corner, lexicon: Lexicon, written: Written) -> int:
    """Write the corner's next lexicon; a corner moved since it was read refuses."""
    values: dict[str, object] = {
        **_where(corner, None),
        "said": Jsonb(dict(lexicon.said)),
        "heard": Jsonb(list(lexicon.heard)),
        "author": written.author,
        "note": written.note,
        "if_version": written.if_version,
    }
    return await _put(pool, "lexicon", PUT_LEXICON, values)


async def standing(pool: Pool, corner: Corner, agent: str) -> Standing:
    """What a call in the corner is built on: each knob from the nearest corner that sets it."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(STANDING, _where(corner, agent))).fetchall()
    tuned = resolve([_tuning(row) for row in rows if row["kind"] == "tuning"])
    words = next((_lexicon(row) for row in rows if row["kind"] == "lexicon"), None)
    return Standing(
        tuning=Tuning() if tuned is None else tuned.value,
        lexicon=Lexicon() if words is None else words.value,
        versions=Versions(
            config=None if tuned is None else tuned.version,
            lexicon=None if words is None else words.version,
        ),
    )


async def every_tuning(pool: Pool, corner: Corner) -> dict[str, Tuning]:
    """Every agent's tuning as it stands in the corner."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(EVERY_AGENT, _where(corner, None))).fetchall()
    chains: dict[str, list[Kept[Tuning]]] = {}
    for row in rows:
        chains.setdefault(row["agent"], []).append(_tuning(row))
    resolved = {agent: resolve(chain) for agent, chain in chains.items()}
    return {agent: kept.value for agent, kept in resolved.items() if kept is not None}


async def tuning_corners(pool: Pool, corner: Corner, agent: str) -> Corners[Tuning]:
    """The agent's tuning as yours, the team's and production's, none falling through."""
    return Corners(
        yours=await _newest_tuning(pool, corner, agent) if corner.holder else None,
        team=await _newest_tuning(pool, replace(corner, holder=THE_ORGS_OWN), agent),
        production=await _newest_tuning(pool, Corner(corner.org, PRODUCTION), agent),
    )


async def lexicon_corners(pool: Pool, corner: Corner) -> Corners[Lexicon]:
    """The lexicon as yours, the team's and production's, none falling through."""
    return Corners(
        yours=await _newest_lexicon(pool, corner) if corner.holder else None,
        team=await _newest_lexicon(pool, replace(corner, holder=THE_ORGS_OWN)),
        production=await _newest_lexicon(pool, Corner(corner.org, PRODUCTION)),
    )


# A knob is set when it is not None, so a falsy value is set and does not fall through:
# `bases: []` is "no bases", not "the team's". The version is the nearest row that set any.
def resolve(chain: Sequence[Kept[Tuning]]) -> Kept[Tuning] | None:
    """Merge a nearest-first chain knob by knob; None when no row sets any knob."""
    supplying = [(row, stored) for row in chain if (stored := _stored(row.value))]
    if not supplying:
        return None
    merged: JsonObject = {}
    for _row, stored in reversed(supplying):
        merged.update(stored)
    nearest, _ = supplying[0]
    return replace(nearest, value=TUNING.validate_python(merged))


async def _newest_tuning(pool: Pool, corner: Corner, agent: str) -> Kept[Tuning] | None:
    rows = await _history(pool, "agent_config", corner, agent, 1)
    return _tuning(rows[0]) if rows else None


async def _newest_lexicon(pool: Pool, corner: Corner) -> Kept[Lexicon] | None:
    rows = await _history(pool, "lexicon", corner, None, 1)
    return _lexicon(rows[0]) if rows else None


async def _history(
    pool: Pool, table: Table, corner: Corner, agent: str | None, limit: int
) -> list[DictRow]:
    query = HISTORY.format(**_parts(table))
    async with pool.connection() as connection:
        values = {**_where(corner, agent), "limit": limit}
        return await (await connection.execute(query, values)).fetchall()


async def _at(
    pool: Pool, table: Table, corner: Corner, agent: str | None, version: int
) -> DictRow | None:
    query = AT.format(**_parts(table))
    async with pool.connection() as connection:
        values = {**_where(corner, agent), "version": version}
        return await (await connection.execute(query, values)).fetchone()


async def _put(
    pool: Pool, table: Table, statement: LiteralString, values: dict[str, object]
) -> int:
    async with pool.connection() as connection:
        row = await (await connection.execute(statement, values)).fetchone()
        if row is not None:
            return int(row["version"])
        raise Conflict(MOVED.format(newest=await _newest(connection, table, values)))


async def _newest(connection: Connection, table: Table, values: dict[str, object]) -> int:
    row = await (await connection.execute(NEWEST.format(**_parts(table)), values)).fetchone()
    return 0 if row is None else int(row["newest"])


def _parts(table: Table) -> dict[str, sql.Composable]:
    return {"table": sql.Identifier(table), "value": VALUE[table], "agent": OF_THE_AGENT[table]}


def _where(corner: Corner, agent: str | None) -> dict[str, object]:
    return {"org": corner.org, "env": corner.env, "holder": corner.holder, "agent": agent}


def _stored(tuning: Tuning) -> JsonObject:
    dumped: JsonObject = TUNING.dump_python(tuning, mode="json", exclude_none=True)
    return dumped


def _tuning(row: DictRow) -> Kept[Tuning]:
    return _kept(row, TUNING.validate_python(row["value"]))


def _lexicon(row: DictRow) -> Kept[Lexicon]:
    value: dict[str, Json] = row["value"]
    said = value.get("said") or {}
    heard = value.get("heard") or []
    lexicon = Lexicon(
        said={str(word): str(spoken) for word, spoken in dict(said).items()}
        if isinstance(said, dict)
        else {},
        heard=tuple(str(word) for word in heard) if isinstance(heard, list) else (),
    )
    return _kept(row, lexicon)


def _kept[T](row: DictRow, value: T) -> Kept[T]:
    return Kept(
        holder=row["holder"],
        version=row["version"],
        author=row["author"],
        note=row["note"],
        set_at=row["set_at"],
        value=value,
    )

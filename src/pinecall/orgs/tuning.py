"""Where an agent's tuning and the org's lexicon are kept: a row a version, a world, a corner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import TypeAdapter

from pinecall._exceptions import PinecallError
from pinecall.log.store import Pool
from pinecall.orgs.lexicon import (
    LEXICON_AT,
    LEXICON_HISTORY,
    NEWEST_LEXICON,
    OWN_LEXICON,
    PUT_LEXICON,
    a_lexicon,
)
from pinecall.orgs.resolving import TUNING, as_json, corners, resolved
from pinecall.types import Env, Kept, Lexicon, Tuning, whose

# The adapter this store reads a lexicon back through and writes one out through; a tuning's own
# is `resolving.TUNING`, where what "set" means is said once for the column and the fall-through.
LEXICON: TypeAdapter[Lexicon] = TypeAdapter(Lexicon)

# What a write says when the corner is not at the version the writer read. Two people saving the
# same agent from two screens: the second is told, never quietly written over the first.
MOVED = "this corner is at v{newest} now, not the version you read: read it again, then set again"


class VersionMoved(PinecallError):
    """The corner's newest is not the one the writer saw, or another writer got there first."""

    def __init__(self, newest: int) -> None:
        super().__init__(MOVED.format(newest=newest))
        self.newest = newest


# The corner chain, nearest first: this holder's newest and the org's own newest, which every
# corner falls back to — the rule 0021 gave knowledge, for the same reason: nobody joins a team to
# an agent with no voice. `''` sorts before any member id, and DESC puts yours first; what the two
# rows resolve to, knob by knob, is `orgs/resolving.py`. A write is one INSERT whose version is
# born inside it: the aggregate over the corner yields one row even over none, HAVING is the
# if_version gate, and two writers computing the same number both hit the primary key — the first
# lands, the second's RETURNING is empty. No lock, no transaction, and no driver exception here,
# because this package may not name the driver (log/store/pool.py).
_CHAIN = """
SELECT DISTINCT ON (holder) holder, version, config, author, note, set_at
  FROM agent_config
 WHERE org = $1 AND env = $2 AND agent = $3 AND holder IN ($4, '')
 ORDER BY holder DESC, version DESC
"""

_OWN = """
SELECT holder, version, config, author, note, set_at
  FROM agent_config
 WHERE org = $1 AND env = $2 AND agent = $3 AND holder = $4
 ORDER BY version DESC
 LIMIT 1
"""

_AT = """
SELECT holder, version, config, author, note, set_at
  FROM agent_config
 WHERE org = $1 AND env = $2 AND agent = $3 AND holder IN ($4, '') AND version = $5
 ORDER BY holder DESC
 LIMIT 1
"""

_HISTORY = """
SELECT holder, version, config, author, note, set_at
  FROM agent_config
 WHERE org = $1 AND env = $2 AND agent = $3 AND holder = $4
 ORDER BY version DESC
 LIMIT $5
"""

# Every agent's chain in this world, the same two corners per agent and in the same order, for the
# same resolution. What the knowledge screen answers "who reads this base" from.
_EVERY_CHAIN = """
SELECT DISTINCT ON (agent, holder) agent, holder, version, config, author, note, set_at
  FROM agent_config
 WHERE org = $1 AND env = $2 AND holder IN ($3, '')
 ORDER BY agent, holder DESC, version DESC
"""

_PUT = """
INSERT INTO agent_config (org, env, holder, agent, version, config, author, note)
SELECT $1, $2, $3, $4, coalesce(max(version), 0) + 1, $5::jsonb, $6, $7
  FROM agent_config
 WHERE org = $1 AND env = $2 AND holder = $3 AND agent = $4
HAVING $8::integer IS NULL OR coalesce(max(version), 0) = $8
ON CONFLICT (org, env, holder, agent, version) DO NOTHING
RETURNING version
"""

# How many versions a history answers when nobody said: a screen's page, not the whole table.
HISTORY_LIMIT = 50


class MemoryTuning:
    """A gateway with no pool: the versions live as long as the process, as the knobs once did."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str, str], list[Kept[Tuning]]] = {}
        self._words: dict[tuple[str, str, str], list[Kept[Lexicon]]] = {}

    async def newest(
        self, org: str, env: Env, holder: str | None, agent: str
    ) -> Kept[Tuning] | None:
        """What this corner reads: each knob from the nearest corner that sets it, else None."""
        return resolved(await self._chain(org, env, holder, agent))

    async def _chain(
        self, org: str, env: Env, holder: str | None, agent: str
    ) -> list[Kept[Tuning]]:
        """The newest row of each corner this key reads through, nearest first."""
        found = [await self.own(org, env, corner, agent) for corner in corners(holder)]
        return [row for row in found if row is not None]

    async def own(self, org: str, env: Env, holder: str, agent: str) -> Kept[Tuning] | None:
        """This corner's newest and nothing else's; None when it set nothing."""
        rows = self._rows.get((org, env, holder, agent), [])
        return rows[-1] if rows else None

    async def at(
        self, org: str, env: Env, holder: str | None, agent: str, version: int
    ) -> Kept[Tuning] | None:
        """One version, the corner's own if it has it, else the org's own."""
        for corner in (whose(holder), whose(None)):
            for row in self._rows.get((org, env, corner, agent), []):
                if row.version == version:
                    return row
        return None

    async def history(
        self, org: str, env: Env, holder: str, agent: str, limit: int = HISTORY_LIMIT
    ) -> list[Kept[Tuning]]:
        """This corner's versions, newest first."""
        return list(reversed(self._rows.get((org, env, holder, agent), [])))[:limit]

    async def every_newest(self, org: str, env: Env, holder: str | None) -> dict[str, Kept[Tuning]]:
        """Every agent in this world by slug, each as this corner reads it."""
        agents = {slug for (o, e, _h, slug) in self._rows if (o, e) == (org, env)}
        found = {slug: await self.newest(org, env, holder, slug) for slug in sorted(agents)}
        return {slug: row for slug, row in found.items() if row is not None}

    async def put(
        self,
        org: str,
        env: Env,
        holder: str,
        agent: str,
        tuning: Tuning,
        *,
        author: str,
        note: str | None,
        if_version: int | None,
    ) -> int:
        """A new version in this corner, numbered after its last; VersionMoved when it moved."""
        rows = self._rows.setdefault((org, env, holder, agent), [])
        return _appended(rows, holder, tuning, author, note, if_version)

    async def newest_lexicon(self, org: str, env: Env, holder: str | None) -> Kept[Lexicon] | None:
        """The corner's own newest lexicon, else the org's own; None when neither set one."""
        return await self.own_lexicon(org, env, whose(holder)) or await self.own_lexicon(
            org, env, whose(None)
        )

    async def own_lexicon(self, org: str, env: Env, holder: str) -> Kept[Lexicon] | None:
        """This corner's newest lexicon and nothing else's."""
        rows = self._words.get((org, env, holder), [])
        return rows[-1] if rows else None

    async def lexicon_at(
        self, org: str, env: Env, holder: str | None, version: int
    ) -> Kept[Lexicon] | None:
        """One version of the lexicon, the corner's own if it has it, else the org's own."""
        for corner in (whose(holder), whose(None)):
            for row in self._words.get((org, env, corner), []):
                if row.version == version:
                    return row
        return None

    async def lexicon_history(
        self, org: str, env: Env, holder: str, limit: int = HISTORY_LIMIT
    ) -> list[Kept[Lexicon]]:
        """This corner's lexicon versions, newest first."""
        return list(reversed(self._words.get((org, env, holder), [])))[:limit]

    async def put_lexicon(
        self,
        org: str,
        env: Env,
        holder: str,
        lexicon: Lexicon,
        *,
        author: str,
        note: str | None,
        if_version: int | None,
    ) -> int:
        """A new lexicon version in this corner; VersionMoved when the corner moved on."""
        rows = self._words.setdefault((org, env, holder), [])
        return _appended(rows, holder, lexicon, author, note, if_version)


def _appended[T](
    rows: list[Kept[T]],
    holder: str,
    value: T,
    author: str,
    note: str | None,
    if_version: int | None,
) -> int:
    """The memory store's INSERT: the same if_version gate the statement has, with no await."""
    standing = len(rows)
    if if_version is not None and if_version != standing:
        raise VersionMoved(standing)
    rows.append(Kept(holder, standing + 1, author, note, datetime.now(UTC), value))
    return standing + 1


class PostgresTuning:
    """The two tables: agent_config, one row a version per corner, and lexicon, the org's words."""

    def __init__(self, pool: Pool) -> None:
        self._pool = pool

    async def newest(
        self, org: str, env: Env, holder: str | None, agent: str
    ) -> Kept[Tuning] | None:
        """What this corner reads: each knob from the nearest corner that sets it, else None."""
        rows = await self._pool.fetch(_CHAIN, org, env, agent, whose(holder))
        return resolved([_a_tuning(row) for row in rows])

    async def own(self, org: str, env: Env, holder: str, agent: str) -> Kept[Tuning] | None:
        """This corner's newest and nothing else's; None when it set nothing."""
        row = await self._pool.fetchrow(_OWN, org, env, agent, holder)
        return None if row is None else _a_tuning(row)

    async def at(
        self, org: str, env: Env, holder: str | None, agent: str, version: int
    ) -> Kept[Tuning] | None:
        """One version, the corner's own if it has it, else the org's own."""
        row = await self._pool.fetchrow(_AT, org, env, agent, whose(holder), version)
        return None if row is None else _a_tuning(row)

    async def history(
        self, org: str, env: Env, holder: str, agent: str, limit: int = HISTORY_LIMIT
    ) -> list[Kept[Tuning]]:
        """This corner's versions, newest first."""
        rows = await self._pool.fetch(_HISTORY, org, env, agent, holder, limit)
        return [_a_tuning(row) for row in rows]

    async def every_newest(self, org: str, env: Env, holder: str | None) -> dict[str, Kept[Tuning]]:
        """Every agent in this world by slug, each as this corner reads it."""
        rows = await self._pool.fetch(_EVERY_CHAIN, org, env, whose(holder))
        chains: dict[str, list[Kept[Tuning]]] = {}
        for row in rows:
            chains.setdefault(str(row["agent"]), []).append(_a_tuning(row))
        read = {slug: resolved(chain) for slug, chain in chains.items()}
        return {slug: row for slug, row in read.items() if row is not None}

    async def put(
        self,
        org: str,
        env: Env,
        holder: str,
        agent: str,
        tuning: Tuning,
        *,
        author: str,
        note: str | None,
        if_version: int | None,
    ) -> int:
        """A new version in this corner, numbered after its last; VersionMoved when it moved."""
        row = await self._pool.fetchrow(
            _PUT, org, env, holder, agent, json.dumps(as_json(tuning)), author, note, if_version
        )
        if row is None:
            standing = await self.own(org, env, holder, agent)
            raise VersionMoved(0 if standing is None else standing.version)
        return int(row["version"])

    async def newest_lexicon(self, org: str, env: Env, holder: str | None) -> Kept[Lexicon] | None:
        """The corner's own newest lexicon, else the org's own; None when neither set one."""
        row = await self._pool.fetchrow(NEWEST_LEXICON, org, env, whose(holder))
        return None if row is None else a_lexicon(row)

    async def own_lexicon(self, org: str, env: Env, holder: str) -> Kept[Lexicon] | None:
        """This corner's newest lexicon and nothing else's."""
        row = await self._pool.fetchrow(OWN_LEXICON, org, env, holder)
        return None if row is None else a_lexicon(row)

    async def lexicon_at(
        self, org: str, env: Env, holder: str | None, version: int
    ) -> Kept[Lexicon] | None:
        """One version of the lexicon, the corner's own if it has it, else the org's own."""
        row = await self._pool.fetchrow(LEXICON_AT, org, env, whose(holder), version)
        return None if row is None else a_lexicon(row)

    async def lexicon_history(
        self, org: str, env: Env, holder: str, limit: int = HISTORY_LIMIT
    ) -> list[Kept[Lexicon]]:
        """This corner's lexicon versions, newest first."""
        rows = await self._pool.fetch(LEXICON_HISTORY, org, env, holder, limit)
        return [a_lexicon(row) for row in rows]

    async def put_lexicon(
        self,
        org: str,
        env: Env,
        holder: str,
        lexicon: Lexicon,
        *,
        author: str,
        note: str | None,
        if_version: int | None,
    ) -> int:
        """A new lexicon version in this corner; VersionMoved when the corner moved on."""
        row = await self._pool.fetchrow(
            PUT_LEXICON,
            org,
            env,
            holder,
            json.dumps(dict(lexicon.said)),
            json.dumps(list(lexicon.heard)),
            author,
            note,
            if_version,
        )
        if row is None:
            standing = await self.own_lexicon(org, env, holder)
            raise VersionMoved(0 if standing is None else standing.version)
        return int(row["version"])


# This pool's connections were never taught the jsonb codec (only the log's own are), so jsonb is
# text going out and text coming back — the same reading orgs/box.py and evals/runs.py do.
def _a_tuning(row: Mapping[str, Any]) -> Kept[Tuning]:
    """One row as the store hands it back: the JSON read through the shape's own adapter."""
    return Kept(
        holder=str(row["holder"]),
        version=int(row["version"]),
        author=str(row["author"]),
        note=row["note"],
        set_at=row["set_at"],
        value=TUNING.validate_python(json.loads(str(row["config"]))),
    )


type TuningStore = MemoryTuning | PostgresTuning


def tuning_for(pool: Pool | None) -> TuningStore:
    """The tables when there is a database, and the process's own memory when there is none."""
    return MemoryTuning() if pool is None else PostgresTuning(pool)

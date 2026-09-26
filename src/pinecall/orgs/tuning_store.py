"""Where an agent's tuning and the org's lexicon are kept: a row a version, a world, a corner."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pinecall.log.store import Pool
from pinecall.orgs.lexicon import LEXICON_STATEMENTS, a_lexicon, lexicon_columns
from pinecall.orgs.tuning_resolution import TUNING, as_json, resolved
from pinecall.orgs.versions import (
    Corner,
    MemoryVersions,
    PostgresVersions,
    Statements,
    Versions,
)
from pinecall.orgs.versions import (
    VersionMoved as VersionMoved,
)
from pinecall.types import Env, Kept, Lexicon, Tuning, whose

# The corner chain, nearest first: this holder's newest and the org's own newest, which every
# corner falls back to — the rule 0021 gave knowledge, for the same reason: nobody joins a team to
# an agent with no voice. `''` sorts before any member id, and DESC puts yours first; what the two
# rows resolve to, knob by knob, is `orgs/tuning_resolution.py`. The statements take the corner as
# orgs/versions.py spells it: $1 org, $2 env, $3 holder, $4 agent.
_COLUMNS = "holder, version, config, author, note, set_at"

TUNING_STATEMENTS = Statements(
    own=f"""
SELECT {_COLUMNS}
  FROM agent_config
 WHERE org = $1 AND env = $2 AND holder = $3 AND agent = $4
 ORDER BY version DESC
 LIMIT 1
""",
    chain=f"""
SELECT DISTINCT ON (holder) {_COLUMNS}
  FROM agent_config
 WHERE org = $1 AND env = $2 AND holder IN ($3, '') AND agent = $4
 ORDER BY holder DESC, version DESC
""",
    at=f"""
SELECT {_COLUMNS}
  FROM agent_config
 WHERE org = $1 AND env = $2 AND holder IN ($3, '') AND agent = $4 AND version = $5
 ORDER BY holder DESC
 LIMIT 1
""",
    history=f"""
SELECT {_COLUMNS}
  FROM agent_config
 WHERE org = $1 AND env = $2 AND holder = $3 AND agent = $4
 ORDER BY version DESC
 LIMIT $5
""",
    put="""
INSERT INTO agent_config (org, env, holder, agent, version, config, author, note)
SELECT $1, $2, $3, $4, coalesce(max(version), 0) + 1, $5::jsonb, $6, $7
  FROM agent_config
 WHERE org = $1 AND env = $2 AND holder = $3 AND agent = $4
HAVING $8::integer IS NULL OR coalesce(max(version), 0) = $8
ON CONFLICT (org, env, holder, agent, version) DO NOTHING
RETURNING version
""",
    # Every agent's chain in this world, the same two corners per agent and in the same order,
    # for the same resolution. What the knowledge screen answers "who reads this base" from.
    every_chain=f"""
SELECT DISTINCT ON (agent, holder) agent, {_COLUMNS}
  FROM agent_config
 WHERE org = $1 AND env = $2 AND holder IN ($3, '')
 ORDER BY agent, holder DESC, version DESC
""",
)

# How many versions a history answers when nobody said: a screen's page, not the whole table.
HISTORY_LIMIT = 50


class TuningStore:
    """The two tables — agent_config, one row a version per corner, and lexicon, the org's words —
    over whichever versions they are kept in; the fall-through between corners is here, once."""

    def __init__(self, tunings: Versions[Tuning], lexicons: Versions[Lexicon]) -> None:
        self._tunings = tunings
        self._lexicons = lexicons

    async def newest(
        self, org: str, env: Env, holder: str | None, agent: str
    ) -> Kept[Tuning] | None:
        """What this corner reads: each knob from the nearest corner that sets it, else None."""
        return resolved(await self._tunings.chain(Corner(org, env, whose(holder), agent)))

    async def own(self, org: str, env: Env, holder: str, agent: str) -> Kept[Tuning] | None:
        """This corner's newest and nothing else's; None when it set nothing."""
        return await self._tunings.own(Corner(org, env, holder, agent))

    async def at(
        self, org: str, env: Env, holder: str | None, agent: str, version: int
    ) -> Kept[Tuning] | None:
        """One version, the corner's own if it has it, else the org's own."""
        return await self._tunings.at(Corner(org, env, whose(holder), agent), version)

    async def history(
        self, org: str, env: Env, holder: str, agent: str, limit: int = HISTORY_LIMIT
    ) -> list[Kept[Tuning]]:
        """This corner's versions, newest first."""
        return await self._tunings.history(Corner(org, env, holder, agent), limit)

    async def every_newest(self, org: str, env: Env, holder: str | None) -> dict[str, Kept[Tuning]]:
        """Every agent in this world by slug, each as this corner reads it."""
        chains = await self._tunings.every_chain(org, env, whose(holder))
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
        corner = Corner(org, env, holder, agent)
        return await self._tunings.put(
            corner, tuning, author=author, note=note, if_version=if_version
        )

    async def newest_lexicon(self, org: str, env: Env, holder: str | None) -> Kept[Lexicon] | None:
        """The corner's own newest lexicon, else the org's own; None when neither set one."""
        chain = await self._lexicons.chain(Corner(org, env, whose(holder)))
        return chain[0] if chain else None

    async def own_lexicon(self, org: str, env: Env, holder: str) -> Kept[Lexicon] | None:
        """This corner's newest lexicon and nothing else's."""
        return await self._lexicons.own(Corner(org, env, holder))

    async def lexicon_at(
        self, org: str, env: Env, holder: str | None, version: int
    ) -> Kept[Lexicon] | None:
        """One version of the lexicon, the corner's own if it has it, else the org's own."""
        return await self._lexicons.at(Corner(org, env, whose(holder)), version)

    async def lexicon_history(
        self, org: str, env: Env, holder: str, limit: int = HISTORY_LIMIT
    ) -> list[Kept[Lexicon]]:
        """This corner's lexicon versions, newest first."""
        return await self._lexicons.history(Corner(org, env, holder), limit)

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
        corner = Corner(org, env, holder)
        return await self._lexicons.put(
            corner, lexicon, author=author, note=note, if_version=if_version
        )


class MemoryTuning(TuningStore):
    """A gateway with no pool: the versions live as long as the process, as the knobs once did."""

    def __init__(self) -> None:
        super().__init__(MemoryVersions[Tuning](), MemoryVersions[Lexicon]())


class PostgresTuning(TuningStore):
    """The two tables in Postgres, read on every request: one gateway sets, every other reads."""

    def __init__(self, pool: Pool) -> None:
        super().__init__(
            PostgresVersions(pool, TUNING_STATEMENTS, _a_tuning, _tuning_columns),
            PostgresVersions(pool, LEXICON_STATEMENTS, a_lexicon, lexicon_columns),
        )


# This pool's connections were never taught the jsonb codec (only the log's own are), so jsonb is
# text going out and text coming back — the same reading orgs/box_settings.py and evals/run_store.py
# do.
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


def _tuning_columns(tuning: Tuning) -> tuple[str]:
    """The one jsonb column a tuning is written as: every knob that is set."""
    return (json.dumps(as_json(tuning)),)


def tuning_for(pool: Pool | None) -> TuningStore:
    """The tables when there is a database, and the process's own memory when there is none."""
    return MemoryTuning() if pool is None else PostgresTuning(pool)

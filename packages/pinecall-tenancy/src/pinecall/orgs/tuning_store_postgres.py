"""The tuning store over two Postgres tables: agent_config and lexicon, one row a version."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pinecall.db import Pool
from pinecall.orgs.lexicon import LEXICON_STATEMENTS, lexicon_columns, lexicon_from_row
from pinecall.orgs.tuning_resolution import TUNING, tuning_json
from pinecall.orgs.tuning_store import TuningStore
from pinecall.orgs.versions import Statements
from pinecall.orgs.versions import VersionMoved as VersionMoved
from pinecall.orgs.versions_postgres import PostgresVersions
from pinecall.types import Kept, Tuning

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


class PostgresTuning(TuningStore):
    """The two tables in Postgres, read on every request: one gateway sets, every other reads."""

    def __init__(self, pool: Pool) -> None:
        super().__init__(
            PostgresVersions(pool, TUNING_STATEMENTS, _a_tuning, _tuning_columns),
            PostgresVersions(pool, LEXICON_STATEMENTS, lexicon_from_row, lexicon_columns),
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
    return (json.dumps(tuning_json(tuning)),)

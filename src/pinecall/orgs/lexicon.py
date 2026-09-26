"""The lexicon's table: the org's words per world and corner, versioned as a tuning is."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pinecall.orgs.versions import Statements
from pinecall.types import Kept, Lexicon

# The lexicon is the org's and not one agent's, so its rows have no agent: the same statements
# over the same questions as agent_config's, one column fewer ($1 org, $2 env, $3 holder).
_COLUMNS = "holder, version, said, heard, author, note, set_at"

LEXICON_STATEMENTS = Statements(
    own=f"""
SELECT {_COLUMNS}
  FROM lexicon
 WHERE org = $1 AND env = $2 AND holder = $3
 ORDER BY version DESC
 LIMIT 1
""",
    chain=f"""
SELECT DISTINCT ON (holder) {_COLUMNS}
  FROM lexicon
 WHERE org = $1 AND env = $2 AND holder IN ($3, '')
 ORDER BY holder DESC, version DESC
""",
    at=f"""
SELECT {_COLUMNS}
  FROM lexicon
 WHERE org = $1 AND env = $2 AND holder IN ($3, '') AND version = $4
 ORDER BY holder DESC
 LIMIT 1
""",
    history=f"""
SELECT {_COLUMNS}
  FROM lexicon
 WHERE org = $1 AND env = $2 AND holder = $3
 ORDER BY version DESC
 LIMIT $4
""",
    put="""
INSERT INTO lexicon (org, env, holder, version, said, heard, author, note)
SELECT $1, $2, $3, coalesce(max(version), 0) + 1, $4::jsonb, $5::jsonb, $6, $7
  FROM lexicon
 WHERE org = $1 AND env = $2 AND holder = $3
HAVING $8::integer IS NULL OR coalesce(max(version), 0) = $8
ON CONFLICT (org, env, holder, version) DO NOTHING
RETURNING version
""",
)


def a_lexicon(row: Mapping[str, Any]) -> Kept[Lexicon]:
    """One lexicon row as the store hands it back."""
    return Kept(
        holder=str(row["holder"]),
        version=int(row["version"]),
        author=str(row["author"]),
        note=row["note"],
        set_at=row["set_at"],
        value=Lexicon(
            said=json.loads(str(row["said"])), heard=tuple(json.loads(str(row["heard"])))
        ),
    )


def lexicon_columns(lexicon: Lexicon) -> tuple[str, str]:
    """The two jsonb columns a lexicon is written as: what is said, and what is heard."""
    return json.dumps(dict(lexicon.said)), json.dumps(list(lexicon.heard))

"""The lexicon's statements: the org's words per world and corner, versioned as a tuning is."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pinecall.types import Kept, Lexicon

# The lexicon is the org's and not one agent's, so its rows have no agent: the same four
# statements over the same three questions, one column fewer.
NEWEST_LEXICON = """
SELECT holder, version, said, heard, author, note, set_at
  FROM lexicon
 WHERE org = $1 AND env = $2 AND holder IN ($3, '')
 ORDER BY holder DESC, version DESC
 LIMIT 1
"""

OWN_LEXICON = """
SELECT holder, version, said, heard, author, note, set_at
  FROM lexicon
 WHERE org = $1 AND env = $2 AND holder = $3
 ORDER BY version DESC
 LIMIT 1
"""

LEXICON_AT = """
SELECT holder, version, said, heard, author, note, set_at
  FROM lexicon
 WHERE org = $1 AND env = $2 AND holder IN ($3, '') AND version = $4
 ORDER BY holder DESC
 LIMIT 1
"""

LEXICON_HISTORY = """
SELECT holder, version, said, heard, author, note, set_at
  FROM lexicon
 WHERE org = $1 AND env = $2 AND holder = $3
 ORDER BY version DESC
 LIMIT $4
"""

PUT_LEXICON = """
INSERT INTO lexicon (org, env, holder, version, said, heard, author, note)
SELECT $1, $2, $3, coalesce(max(version), 0) + 1, $4::jsonb, $5::jsonb, $6, $7
  FROM lexicon
 WHERE org = $1 AND env = $2 AND holder = $3
HAVING $8::integer IS NULL OR coalesce(max(version), 0) = $8
ON CONFLICT (org, env, holder, version) DO NOTHING
RETURNING version
"""


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

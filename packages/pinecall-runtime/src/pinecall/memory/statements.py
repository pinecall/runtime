"""Every SQL statement PgvectorMemory runs, named once: the reads, both branches, the writes."""

from __future__ import annotations

from pinecall.types import CANDIDATES_PER_BRANCH

# The columns a Fact is read off, spelled once for the four reads below.
COLUMNS = "id, contact, text, category, source_call, valid_from, invalidated_at, confidence"

# What "current" means for a recall: with no as_of, the rows nobody invalidated — the partial
# index's own WHERE; with one, the rows that held at that moment, which is the bi-temporal read.
HELD = """
WHERE org = $1 AND env = $2 AND holder = $3 AND contact = $4
  AND (($5::timestamptz IS NULL AND invalidated_at IS NULL)
       OR ($5::timestamptz IS NOT NULL AND valid_from <= $5
           AND (invalidated_at IS NULL OR invalidated_at > $5)))
"""

# The dense branch: cosine over the halved vectors, the HNSW index's own operator class, and only
# over the rows THIS embedder wrote — a vector of another model is a number of the same width and
# nothing more. The words branch below carries no such filter on purpose: BM25 reads the text and
# not the vector, so a fact written under an older embedder is still recalled by what it says.
BY_VECTOR = f"""
SELECT {COLUMNS} FROM contact_memories
{HELD}
  AND model = $7
ORDER BY embedding <=> $6::text::halfvec
LIMIT {CANDIDATES_PER_BRANCH}
"""

# The sparse branch: pg_textsearch's `<@>` is the NEGATIVE BM25 score, lower is better, and a row
# with no term in common with the query is not returned at all. The query is parsed against the
# named index, whose text configuration (0008: spanish) stems both sides the same way.
BY_WORDS = f"""
SELECT {COLUMNS} FROM contact_memories
{HELD}
ORDER BY text <@> to_bm25query($6, 'contact_memories_text_bm25')
LIMIT {CANDIDATES_PER_BRANCH}
"""

CURRENT = f"""
SELECT {COLUMNS} FROM contact_memories
WHERE org = $1 AND env = $2 AND holder = $3 AND contact = $4 AND invalidated_at IS NULL
ORDER BY valid_from, id
"""

EVERY_ROW = f"""
SELECT {COLUMNS} FROM contact_memories
WHERE org = $1 AND env = $2 AND holder = $3 AND contact = $4
ORDER BY (invalidated_at IS NULL) DESC, valid_from DESC, id
"""

# What an agent's calls taught, across its contacts: the fact's own source call is a head row, and
# that row says which agent took it. Current facts only, newest first; `$5` is the words as a LIKE
# pattern, `$6`/`$7` the cursor — the last fact of the page before.
TAUGHT_BY = f"""
SELECT {COLUMNS}, head.agent AS taught_by FROM contact_memories memory
JOIN call_log_head head ON head.log = memory.source_call
WHERE memory.org = $1 AND memory.env = $2 AND memory.holder = $3
  AND ($4::text IS NULL OR head.agent = $4)
  AND memory.invalidated_at IS NULL
  AND ($5::text IS NULL OR memory.text ILIKE '%' || $5 || '%'
       OR memory.contact ILIKE '%' || $5 || '%' OR memory.category ILIKE '%' || $5 || '%')
  AND ($6::timestamptz IS NULL OR (memory.valid_from, memory.id) < ($6, $7::uuid))
ORDER BY memory.valid_from DESC, memory.id DESC
LIMIT $8
"""

ENDED = """
UPDATE contact_memories SET invalidated_at = $5
WHERE id = $4::uuid AND org = $1 AND env = $2 AND holder = $3 AND invalidated_at IS NULL
RETURNING id
"""

# The count of what went, as a row and never off the command tag.
FORGET = """
WITH gone AS (
    DELETE FROM contact_memories WHERE org = $1 AND env = $2 AND holder = $3 AND contact = $4
    RETURNING id
)
SELECT count(*) AS forgotten FROM gone
"""

# What the org KEEPS, which is what its quota is about: the current rows, every contact together,
# in BOTH worlds — a fact a test call wrote is a row on the same disk as one a real call wrote. A
# superseded row is history and not a fact the org holds, so the count reads the same partial
# index (org, env, contact) WHERE invalidated_at IS NULL that a recall does, over every env.
KEPT = "SELECT count(*) AS kept FROM contact_memories WHERE org = $1 AND invalidated_at IS NULL"

ADD = """
INSERT INTO contact_memories
    (org, env, holder, contact, text, category, embedding, valid_from, source_call, model)
VALUES ($1, $2, $3, $4, $5, $6, $7::text::halfvec, $8, $9, $10)
RETURNING id
"""

# An update is a new row that supersedes the old one, and the old one's end is written in the
# same statement: the two never disagree, and a fact somebody already superseded takes no row.
UPDATE = """
WITH superseded AS (
    UPDATE contact_memories SET invalidated_at = $8
    WHERE id = $11::uuid AND org = $1 AND env = $2 AND holder = $3 AND contact = $4
      AND invalidated_at IS NULL
    RETURNING id
)
INSERT INTO contact_memories
    (org, env, holder, contact, text, category, embedding, valid_from, source_call, model,
     supersedes)
SELECT $1, $2, $3, $4, $5, $6, $7::text::halfvec, $8, $9, $10, superseded.id FROM superseded
RETURNING id
"""

INVALIDATE = """
UPDATE contact_memories SET invalidated_at = $5
WHERE id = $6::uuid AND org = $1 AND env = $2 AND holder = $3 AND contact = $4
  AND invalidated_at IS NULL
"""

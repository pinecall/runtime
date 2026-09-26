-- 0008: what a contact's calls taught. One row per fact, never deleted: superseded.
--
-- Until here an agent met every caller as a stranger. This table is the contact's memory across
-- calls and across doors: the same person on the phone today and on WhatsApp next week reads as
-- one contact, and what the first call taught is under the memory marker of the second. A fact
-- is a sentence in the caller's language, filed under one of the categories the tenant named
-- in its own words (`MemoryPolicy.remember`), and it is bi-temporal: `valid_from` says since when
-- it held, `invalidated_at` says when it stopped, and `supersedes` points at the row it replaced.
-- Nothing is ever UPDATEd but `invalidated_at`, so the history of a contact is every row, and a
-- question about what memory held on a given day has an answer. The one DELETE is `forget`, the
-- right to be forgotten, which takes every row of the contact at once.
--
-- Recall is hybrid: the dense branch orders by cosine over `embedding` (bge-m3, 1024 wide, halved
-- to fit the HNSW page), the sparse branch by BM25 over `text` in the callers' language, and the
-- two are fused by rank in memory/ranking.py. The bm25 index is NAMED, because pg_textsearch's
-- `to_bm25query(query, index_name)` looks the query's statistics up by that name; the partial
-- btree is the common read, every current fact of one contact. See docs/decisions/memory.md.

-- pgvector's types and pg_textsearch's functions live where the extensions were created, which
-- is public, and a schema of its own — a test's — is not on the path when the runner applies
-- this file. Local to the migration's transaction; on a box the path is public already.
SELECT set_config('search_path', current_schema() || ', public', true);

CREATE TABLE IF NOT EXISTS contact_memories (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org             text NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    contact         text NOT NULL,
    text            text NOT NULL,
    category        text,
    embedding       halfvec(1024) NOT NULL,
    valid_from      timestamptz NOT NULL,
    invalidated_at  timestamptz,
    supersedes      uuid REFERENCES contact_memories (id),
    source_call     text,
    confidence      real NOT NULL DEFAULT 1.0,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS contact_memories_embedding_hnsw
    ON contact_memories USING hnsw (embedding halfvec_cosine_ops);

CREATE INDEX IF NOT EXISTS contact_memories_text_bm25
    ON contact_memories USING bm25 (text) WITH (text_config = 'spanish');

CREATE INDEX IF NOT EXISTS contact_memories_current
    ON contact_memories (org, contact) WHERE invalidated_at IS NULL;

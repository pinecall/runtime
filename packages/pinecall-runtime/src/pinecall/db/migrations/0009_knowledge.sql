-- 0009: the knowledge base. A tenant's files, pushed by name, chunked, embedded and indexed twice.
--
-- A base is a folder as of its last push: `knowledge_bases` has one row per (org, base) saying
-- when it was pushed, how many chunks it became, and which model wrote the vectors at which width
-- — a vector is only comparable to vectors of the same model, so the row says whose they are.
-- `knowledge_chunks` is the base cut into pieces a turn can be handed: each one under a heading
-- path, in the order it was read, with the text the indexes read and the vector the embedder
-- answered. A push replaces the base whole; a chunk is never edited.
--
-- Two indexes answer a search, one per branch of the hybrid: HNSW over the vector for the
-- meaning, BM25 over the text for the words. The BM25 index is named, because pg_textsearch
-- scores a text by the statistics of one index and the query names it —
-- `to_bm25query(<query>, 'knowledge_chunks_text_bm25')`, the query first (verified against 1.4.0:
-- the other order looks the query up as an index and fails). `text <@> query` answers the
-- NEGATIVE BM25 score, so lower is better and 0 is a text none of the query's terms is in. The
-- language is the index's, fixed here: stemming "turnos" to "turno" is what makes a search find
-- them, and a migration reads no setting.

CREATE TABLE IF NOT EXISTS knowledge_bases (
    org         text NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    base        text NOT NULL,
    model       text NOT NULL,
    dimensions  integer NOT NULL,
    chunks      integer NOT NULL,
    pushed_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, base)
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    org         text NOT NULL,
    base        text NOT NULL,
    path        text NOT NULL,
    heading     text,
    ordinal     integer NOT NULL,
    text        text NOT NULL,
    embedding   halfvec(1024) NOT NULL,
    FOREIGN KEY (org, base) REFERENCES knowledge_bases (org, base) ON DELETE CASCADE
);

-- Every search is one base of one org: both branches filter on this before they rank.
CREATE INDEX IF NOT EXISTS knowledge_chunks_by_base
    ON knowledge_chunks (org, base);

CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_hnsw
    ON knowledge_chunks USING hnsw (embedding halfvec_cosine_ops);

CREATE INDEX IF NOT EXISTS knowledge_chunks_text_bm25
    ON knowledge_chunks USING bm25 (text) WITH (text_config = 'spanish');

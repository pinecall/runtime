-- 0018: a contact's facts and a knowledge base are one world's, as the registry and the routes are.
--
-- 0013 gave the KEY a world and namespaced what a key claims — the agents it holds, the doors
-- they answer. What a call WRITES was left in one pile: a developer's test call on a laptop
-- extracted facts about the real contact of a real production call and put them under the same
-- marker, and a `knowledge push` from that laptop replaced the base the telephone answers from.
-- Both are worse than a wrong answer, because both outlive the test.
--
-- So the two tables a call writes carry the world it happened in, and every read filters on it:
-- production recalls production, a laptop recalls its own, and promoting knowledge is a push made
-- with the key the box runs on. Everything already written is production's — there was one world
-- until 0013 and no way to write from the other before it — which is what the backfill means and
-- why the default is dropped straight after: from here the runtime writes the column and no
-- policy lives in the table.
--
-- A base's name is unique per world, not per org, so the primary key of `knowledge_bases` grows
-- the column and the chunks' foreign key follows it. The indexes that a read filters on grow it
-- too, in front of what they already had: every recall is one contact of one org in one world,
-- and every search one base of one org in one world.

ALTER TABLE contact_memories
    ADD COLUMN IF NOT EXISTS env text NOT NULL DEFAULT 'production'
        CHECK (env IN ('production', 'development'));
ALTER TABLE contact_memories ALTER COLUMN env DROP DEFAULT;

DROP INDEX IF EXISTS contact_memories_current;
CREATE INDEX IF NOT EXISTS contact_memories_current
    ON contact_memories (org, env, contact) WHERE invalidated_at IS NULL;

ALTER TABLE knowledge_bases
    ADD COLUMN IF NOT EXISTS env text NOT NULL DEFAULT 'production'
        CHECK (env IN ('production', 'development'));
ALTER TABLE knowledge_chunks
    ADD COLUMN IF NOT EXISTS env text NOT NULL DEFAULT 'production'
        CHECK (env IN ('production', 'development'));

-- The chunks' key is dropped first: it points at the base's, and the base's is about to change.
ALTER TABLE knowledge_chunks DROP CONSTRAINT IF EXISTS knowledge_chunks_org_base_fkey;
ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS knowledge_bases_pkey;
ALTER TABLE knowledge_bases ADD PRIMARY KEY (org, env, base);
ALTER TABLE knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_org_env_base_fkey
    FOREIGN KEY (org, env, base) REFERENCES knowledge_bases (org, env, base) ON DELETE CASCADE;

ALTER TABLE knowledge_bases ALTER COLUMN env DROP DEFAULT;
ALTER TABLE knowledge_chunks ALTER COLUMN env DROP DEFAULT;

DROP INDEX IF EXISTS knowledge_chunks_by_base;
CREATE INDEX IF NOT EXISTS knowledge_chunks_by_base
    ON knowledge_chunks (org, env, base);

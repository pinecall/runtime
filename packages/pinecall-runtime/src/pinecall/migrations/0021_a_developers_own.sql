-- 0021: in development, a contact's facts and a knowledge base are one DEVELOPER's.
--
-- 0018 gave both tables the world they were written in, which stopped a laptop's test call from
-- writing into what the telephone answers from. It left the other half: development was one pile
-- shared by everybody on the team. Three developers of one tenant each hold their own agent
-- (`Held` is `(env, holder, slug)` — 0013, and api/agents/registry.py), and then one of them
-- pushes a knowledge folder and replaces what the other two were testing against, or a test call
-- extracts a fact about a contact the other two are also using. Neither is an error anybody sees:
-- it is a colleague's answer arriving in your call.
--
-- So both tables carry WHOSE corner wrote them, exactly as the registry does. The org's own
-- corner is the empty string and not NULL, because it is part of a key and a NULL in one matches
-- nothing. A production row is always the org's — a person's key opens no `app` there — and so is
-- anything a development key naming nobody wrote, which is CI's. Everything already written is
-- therefore the org's own, which is what the default backfills and why it is dropped straight
-- after: from here the runtime writes the column and no policy lives in the table.
--
-- Knowledge FALLS BACK and memory does not, and the difference is what each one is. A base is a
-- thing somebody wrote down for the agent to read, so a developer who has pushed none still reads
-- the org's — nobody joins a team to an empty knowledge base, and the store resolves that the way
-- `Registry.of()` falls back to the org's corner. A contact's facts are what a CALL learned, and
-- there is no org-wide development call to inherit from: they are the corner's, or nothing.

ALTER TABLE contact_memories ADD COLUMN IF NOT EXISTS holder text NOT NULL DEFAULT '';
ALTER TABLE contact_memories ALTER COLUMN holder DROP DEFAULT;

DROP INDEX IF EXISTS contact_memories_current;
CREATE INDEX IF NOT EXISTS contact_memories_current
    ON contact_memories (org, env, holder, contact) WHERE invalidated_at IS NULL;

ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS holder text NOT NULL DEFAULT '';
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS holder text NOT NULL DEFAULT '';

-- The chunks' key points at the base's, and the base's grows the column.
ALTER TABLE knowledge_chunks DROP CONSTRAINT IF EXISTS knowledge_chunks_org_env_base_fkey;
ALTER TABLE knowledge_bases DROP CONSTRAINT IF EXISTS knowledge_bases_pkey;
ALTER TABLE knowledge_bases ADD PRIMARY KEY (org, env, holder, base);
ALTER TABLE knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_org_env_holder_base_fkey
    FOREIGN KEY (org, env, holder, base) REFERENCES knowledge_bases (org, env, holder, base)
    ON DELETE CASCADE;

ALTER TABLE knowledge_bases ALTER COLUMN holder DROP DEFAULT;
ALTER TABLE knowledge_chunks ALTER COLUMN holder DROP DEFAULT;

DROP INDEX IF EXISTS knowledge_chunks_by_base;
CREATE INDEX IF NOT EXISTS knowledge_chunks_by_base
    ON knowledge_chunks (org, env, holder, base);

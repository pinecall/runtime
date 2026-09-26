-- 0023: the world things are written in is `sandbox`, and `development` is a word freed up.
--
-- `development` was doing two jobs: naming a world, and naming "mine". A team that wanted a
-- shared development deployment had nowhere to put it — the word was taken — and a person reading
-- `env: development` could not tell whether they were looking at somebody's laptop or at the
-- team's box. They were never the same axis: which WORLD a thing is in is this column, and whether
-- a sandbox agent is one person's copy or the team's shared one is whether the key that registered
-- it names a person (api/agents/holding.py). Two worlds, three behaviours.
--
-- Every row moves, in five tables, and the CHECK moves with it. The order matters on a table that
-- HAS rows: the old constraint has to go before a row can hold the new word, and the new one
-- arrives NOT VALID so the write lock is not held while Postgres reads the whole table — then
-- VALIDATE takes a lock nothing else waits behind.

ALTER TABLE api_keys           DROP CONSTRAINT IF EXISTS api_keys_env_check;
ALTER TABLE routes             DROP CONSTRAINT IF EXISTS routes_env_check;
ALTER TABLE contact_memories   DROP CONSTRAINT IF EXISTS contact_memories_env_check;
ALTER TABLE knowledge_bases    DROP CONSTRAINT IF EXISTS knowledge_bases_env_check;
ALTER TABLE knowledge_chunks   DROP CONSTRAINT IF EXISTS knowledge_chunks_env_check;

UPDATE api_keys         SET env = 'sandbox' WHERE env = 'development';
UPDATE routes           SET env = 'sandbox' WHERE env = 'development';
UPDATE contact_memories SET env = 'sandbox' WHERE env = 'development';
UPDATE knowledge_bases  SET env = 'sandbox' WHERE env = 'development';
UPDATE knowledge_chunks SET env = 'sandbox' WHERE env = 'development';

ALTER TABLE api_keys         ADD CONSTRAINT api_keys_env_check
    CHECK (env IN ('production', 'sandbox')) NOT VALID;
ALTER TABLE routes           ADD CONSTRAINT routes_env_check
    CHECK (env IN ('production', 'sandbox')) NOT VALID;
ALTER TABLE contact_memories ADD CONSTRAINT contact_memories_env_check
    CHECK (env IN ('production', 'sandbox')) NOT VALID;
ALTER TABLE knowledge_bases  ADD CONSTRAINT knowledge_bases_env_check
    CHECK (env IN ('production', 'sandbox')) NOT VALID;
ALTER TABLE knowledge_chunks ADD CONSTRAINT knowledge_chunks_env_check
    CHECK (env IN ('production', 'sandbox')) NOT VALID;

ALTER TABLE api_keys         VALIDATE CONSTRAINT api_keys_env_check;
ALTER TABLE routes           VALIDATE CONSTRAINT routes_env_check;
ALTER TABLE contact_memories VALIDATE CONSTRAINT contact_memories_env_check;
ALTER TABLE knowledge_bases  VALIDATE CONSTRAINT knowledge_bases_env_check;
ALTER TABLE knowledge_chunks VALIDATE CONSTRAINT knowledge_chunks_env_check;

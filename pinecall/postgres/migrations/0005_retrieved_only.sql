-- Every chunk is found by its vector: no chunk is kept whole without one. A file keeps the hash of
-- the text it was cut from, so a push embeds only the files that changed; blank means pushed before
-- this migration, and the next push fills it.

-- Nothing ever wrote such a row; the statement makes the two constraints below safe to add.
DELETE FROM knowledge_chunks WHERE mode <> 'retrieved' OR embedding IS NULL;

ALTER TABLE knowledge_chunks DROP COLUMN mode;
ALTER TABLE knowledge_chunks ALTER COLUMN embedding SET NOT NULL;

ALTER TABLE knowledge_files ADD COLUMN sha256 text NOT NULL DEFAULT '';

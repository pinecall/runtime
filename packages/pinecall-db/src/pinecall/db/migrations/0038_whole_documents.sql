-- 0038: a file of a knowledge base may be kept WHOLE, for the static block, beside the chunks a turn searches.
--
-- Until here the file an agent knew by heart travelled inside its class's declaration, on every
-- reconnect, and changing what the agent knew meant a deploy. It is a document of the base now,
-- pushed with the others and marked `whole`: one row, the whole text, no vector — a turn never
-- searches it, because the model already reads it, cached ahead of everything, in the knowledge
-- block of every call. The gateway reads the whole rows of the bases an agent's settings attach
-- and hands their text to the session where the class's file used to go.
--
-- Every row already here is `retrieved`, which is what every row was. The check arrives NOT VALID
-- and is validated apart, as a constraint on a populated table does; the vector column may be
-- NULL from here, for the whole rows alone — the indexes skip a NULL and a search never reads one.

ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS mode text NOT NULL DEFAULT 'retrieved';

ALTER TABLE knowledge_chunks
    ADD CONSTRAINT knowledge_chunks_mode_check CHECK (mode IN ('retrieved', 'whole')) NOT VALID;

ALTER TABLE knowledge_chunks VALIDATE CONSTRAINT knowledge_chunks_mode_check;

-- The one client of this column is the runtime, and every read of it filters mode = 'retrieved'.
-- squawk-ignore ban-drop-not-null
ALTER TABLE knowledge_chunks ALTER COLUMN embedding DROP NOT NULL;

-- 0010: which model wrote a fact's vector, so a box that changes embedder does not recall noise.
--
-- 0009 already gave the knowledge base this column, on the base's row, and a search of a base
-- pushed under another model is refused with a sentence that says to push it again. A contact's
-- facts have no push to redo: they were learned in calls that are over. So the column sits on the
-- ROW, and the dense branch of a recall filters on it — an older fact is simply not a candidate
-- by meaning any more, while BM25 reads the text and still finds it by its words. Nothing is
-- migrated, nothing is deleted, and a box that goes back to the old embedder recalls them all
-- again. The default is the empty string rather than NULL because a filter is an equality and
-- `model = $1` must never be a three-valued question: every row written before this column
-- existed was written by whatever the box then ran, and it is deliberately named by nothing.

ALTER TABLE contact_memories
    ADD COLUMN IF NOT EXISTS model text NOT NULL DEFAULT '';

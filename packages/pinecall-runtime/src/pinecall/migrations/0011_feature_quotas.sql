-- 0011: two more quotas of the same kind, so a plan can switch memory and retrieval off.

-- 0006 gave an org four limits over what it CONSUMES: minutes, messages, agents held, calls at
-- once. These two are over what it KEEPS — the facts memory holds about its contacts, and the
-- chunks its knowledge bases hold — and they are the same mechanism and not a new concept: NULL
-- is no limit, which is what a self-hosted box has because it never writes a row; 0 is a real
-- limit that refuses everything, which is how a free plan has neither feature; a number is a cap.
-- Nothing here prices anything: whoever charges sets the numbers through the operator API.
-- Both are counted by a QUERY and never by a counter column — a count of rows is arithmetic over
-- the tables that already exist, and a counter is a second truth that drifts from them.

ALTER TABLE quotas
    ADD COLUMN IF NOT EXISTS memory_facts     integer,
    ADD COLUMN IF NOT EXISTS knowledge_chunks integer;

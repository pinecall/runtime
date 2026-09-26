-- 0046: which synthetic caller ran this call, projected beside the rest of its facts.
--
-- A simulation is a call like any other, and until now nothing in it said who was playing the
-- caller: the persona lived in the terminal that drove the turns and never reached the log. So
-- the Personas screen could show what a caller IS and nothing about what it has DONE.
--
-- The name is written on the call's own `call.started`, beside `run` — the log is the truth — and
-- this column is the projection of it, folded by log/call_facts.py as every other fact is. NULL is a
-- call nobody was playing: a person, or a simulation from before this.
--
-- No backfill. A persona's name is nowhere in the logs of the calls that already happened, so
-- there is nothing to fold back: every older simulation reads as "no persona", which is what it
-- honestly is. 0026 could restate the fold in SQL because the entries already carried the facts.

ALTER TABLE call_facts ADD COLUMN IF NOT EXISTS persona text;

-- The one question this column answers: a caller's own runs, newest first. The index is on the
-- persona alone — the join to the head row narrows to the org, the world and the corner, exactly
-- as call_facts_by_contact does for the inbox. Partial, because almost every call has no persona.
-- The column was added one statement up and holds no row a write could be waiting on.
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS call_facts_by_persona
    ON call_facts (persona) WHERE persona IS NOT NULL;

-- 0025: one row per call, the facts a list filters on and a day is counted from; and who read what.
--
-- Every question a console asked across calls was answered by folding each call's log: the
-- session list reduced every row it drew, and there was no way to ask "today's calls", "the ones
-- a person took over" or "the ones that say 600 12" short of reducing all of them. A day of calls
-- is thousands of logs; a screen cannot wait for that.
--
-- So the store keeps, beside the head row, the handful of facts those questions read — the door a
-- call came in by, its two numbers and its contact, how it ended and what it cost, how the judges
-- answered, whether a person took part, every agent turn's e2e_latency, when each caller message
-- landed, and the last thing said. It is a PROJECTION: every column is what some entry of the log
-- said, written by the store as it appends that entry (log/facts.py is the one fold, and the
-- memory store runs the very same one), and the log stays the truth. A call from before this
-- migration has no row until 0026 folds it back, and until then reads as a call nobody indexed:
-- listed, and counted in no day.
--
-- The org, the world, the holder, the agent and started_at are NOT copied: they are the head
-- row's, written by the claim and the first append, and every read joins on the call id.
--
-- `thread_reads` is the inbox's read cursor, one per person per contact thread: what a member has
-- read of a contact's calls with one agent, in the corner it was read in. A person's cursor and
-- never the org's, because two people of one team read one inbox and neither has read for the other.

CREATE TABLE IF NOT EXISTS call_facts (
    call         text PRIMARY KEY,
    channel      text,
    direction    text,
    from_number  text,
    to_number    text,
    name         text,
    contact      text,
    spoken       boolean NOT NULL DEFAULT false,
    ended_at     double precision,
    end_reason   text,
    outcome      text,
    cost_eur     double precision,
    judged       integer,
    held         integer,
    passed       boolean,
    reason       text,
    escalated    boolean NOT NULL DEFAULT false,
    promised     boolean NOT NULL DEFAULT false,
    e2e          double precision[] NOT NULL DEFAULT '{}',
    heard_at     double precision[] NOT NULL DEFAULT '{}',
    last_text    text,
    last_at      double precision,
    last_in      boolean
);

-- The inbox groups an agent's calls by contact; the join to the head row narrows to the corner.
-- The table was created three statements up and holds no row a write could be waiting on.
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS call_facts_by_contact ON call_facts (contact) WHERE contact IS NOT NULL;

CREATE TABLE IF NOT EXISTS thread_reads (
    org      text NOT NULL,
    env      text NOT NULL,
    holder   text NOT NULL,
    agent    text NOT NULL,
    reader   text NOT NULL,
    contact  text NOT NULL,
    read_at  double precision NOT NULL,
    PRIMARY KEY (org, env, holder, agent, reader, contact)
);

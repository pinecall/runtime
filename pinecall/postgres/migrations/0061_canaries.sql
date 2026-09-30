-- A version of an agent's settings on a share of its calls (tenancy/canary.py): the newest row of
-- a scope says which version takes `share` % of the scope's calls, each call picked once by its
-- id; every other call runs what the scope would run without that version. A row with no version
-- is the canary cleared. Rows are only ever added, so the canary that stood when a call opened is
-- read back for it (GET /v1/calls/{call}/settings says whether the call was one).
--
-- A new table: CREATE TABLE locks nothing that exists but `orgs`, in SHARE ROW EXCLUSIVE for the
-- foreign key, for the instant of the statement: reads of orgs go on, a write to it waits.

CREATE TABLE agent_canaries (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    env text NOT NULL CHECK (env IN ('production', 'sandbox')),
    holder text NOT NULL,
    agent text NOT NULL,
    version integer CHECK (version > 0),
    share integer CHECK (share BETWEEN 0 AND 100),
    author text NOT NULL,
    note text,
    set_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((version IS NULL) = (share IS NULL))
);

CREATE INDEX agent_canaries_standing ON agent_canaries (org, env, holder, agent, set_at DESC, id DESC);

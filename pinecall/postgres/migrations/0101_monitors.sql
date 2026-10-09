-- A monitor watches one number of the Observability series over a window and says, once a day
-- on the agent's log, when it crosses the line (tenancy/monitors.py, raised at the seal). One
-- row per monitor; `agent` null watches every agent of the world. Expanding only: a new table,
-- under the row-level security every org table has.

CREATE TABLE monitors (
    id text NOT NULL,
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    env text NOT NULL CHECK (env IN ('production', 'sandbox')),
    agent text,
    name text NOT NULL,
    metric text NOT NULL CHECK (metric IN ('e2e_median_s', 'llm_median_s', 'held_rate',
                                           'escalated_rate', 'tool_failure_rate', 'spend_usd',
                                           'calls')),
    above boolean NOT NULL,
    threshold double precision NOT NULL,
    window_days integer NOT NULL CHECK (window_days IN (1, 7, 30)),
    created_by text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    fired_on date,
    fired_value double precision,
    CONSTRAINT monitors_pkey PRIMARY KEY (id)
);

CREATE INDEX monitors_by_scope ON monitors (org, env);

ALTER TABLE monitors ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_scoped ON monitors
    USING (coalesce(current_setting('pinecall.org', true), '') = ''
           OR org = current_setting('pinecall.org', true))
    WITH CHECK (coalesce(current_setting('pinecall.org', true), '') = ''
                OR org = current_setting('pinecall.org', true));

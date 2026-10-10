-- Every judge is a question a model answers (evals/catalog.py, evals/_asking.py). An org's own judge
-- says how it answers (held/broken, one of its choices, a score), when it runs (always, simulations
-- only, or on a trigger question) and what it reads beyond the call; the library's judges are
-- switched on or off per org and per agent; an org may name the model its calls are judged on
-- (`judge_model`, null: the platform's), and on a key of its own its evals are never billed.
-- Expanding only: `runs_on` is read no more and dropped
-- by a later release, and judge_switches is a new table under the row-level security every org
-- table has.

ALTER TABLE agent_judges
    ADD COLUMN answer text NOT NULL DEFAULT 'verdict' CHECK (answer IN ('verdict', 'choice', 'score')),
    ADD COLUMN choices jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN runs_when text NOT NULL DEFAULT 'always'
        CHECK (runs_when IN ('always', 'simulations', 'trigger')),
    ADD COLUMN trigger text NOT NULL DEFAULT '',
    ADD COLUMN reads jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE orgs ADD COLUMN judge_model jsonb;

UPDATE agent_judges SET runs_when = 'simulations' WHERE runs_on = 'simulations';

-- agent '' is the org's own switch, the default for every agent; an agent's row wins over it.
CREATE TABLE judge_switches (
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    agent text NOT NULL DEFAULT '',
    name text NOT NULL,
    is_on boolean NOT NULL,
    author text NOT NULL DEFAULT '',
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT judge_switches_pkey PRIMARY KEY (org, agent, name)
);

ALTER TABLE judge_switches ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_scoped ON judge_switches
    USING (coalesce(current_setting('pinecall.org', true), '') = ''
           OR org = current_setting('pinecall.org', true))
    WITH CHECK (coalesce(current_setting('pinecall.org', true), '') = ''
                OR org = current_setting('pinecall.org', true));

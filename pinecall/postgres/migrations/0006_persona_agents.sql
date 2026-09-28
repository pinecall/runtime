-- A persona is written for the agents it may call; none named is every agent of the org, which is
-- what a row without an agent meant.
ALTER TABLE agent_personas ADD COLUMN agents text[] NOT NULL DEFAULT '{}';
UPDATE agent_personas SET agents = ARRAY[agent] WHERE agent IS NOT NULL;
ALTER TABLE agent_personas DROP COLUMN agent;

-- A run belongs to an org and a world; a row restored without them is listed once they are filled.
ALTER TABLE eval_runs ADD COLUMN org text, ADD COLUMN env text;
CREATE INDEX eval_runs_by_scope ON eval_runs (org, env, started_at DESC);

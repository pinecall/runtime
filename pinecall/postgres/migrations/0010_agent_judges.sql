-- An agent's own judges: a question the org wrote about the agent's job, asked at hang-up beside
-- the runtime's panel. One list per agent for both worlds, as its personas are.
CREATE TABLE agent_judges (
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    agent text NOT NULL,
    name text NOT NULL,
    question text NOT NULL,
    runs_on text NOT NULL DEFAULT 'every-call' CHECK (runs_on IN ('every-call', 'simulations')),
    author text NOT NULL DEFAULT '',
    set_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, agent, name)
);

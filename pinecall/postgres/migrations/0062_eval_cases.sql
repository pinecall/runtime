-- The org's dataset: a real call's caller turns kept as a golden an eval run can play again
-- (evals/dataset.py). A case is tenant data like the call it came from: erasing that call, the
-- contact who made it, or the org erases the case too (tenancy/erasure.py by `source_call`, the
-- foreign key for the org), and the calls retention erases go through the same path.
--
-- A new table: CREATE TABLE locks nothing that exists but `orgs`, in SHARE ROW EXCLUSIVE for the
-- foreign key, for the instant of the statement: reads of orgs go on, a write to it waits.

CREATE TABLE eval_cases (
    id text PRIMARY KEY,
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    agent text NOT NULL,
    name text NOT NULL,
    golden jsonb NOT NULL,
    source_call text NOT NULL,
    source_env text NOT NULL CHECK (source_env IN ('production', 'sandbox')),
    held_out boolean NOT NULL DEFAULT false,
    author text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org, agent, name)
);

CREATE INDEX eval_cases_by_call ON eval_cases (source_call);

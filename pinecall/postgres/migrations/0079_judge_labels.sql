-- What a person said a judge should have answered on a call (evals/calibration.py): one label per
-- call and judge, replaced when labelled again. Each judge's agreement with them is read against
-- the verdict the seal counted (drift_calls), so a judge that disagrees with the people who know
-- is reported, never trusted silently. A label goes with its call's erasure and with the org.
-- A new table: CREATE TABLE locks nothing that exists but `orgs`, in SHARE ROW EXCLUSIVE for the
-- foreign key, for the instant of the statement: reads of orgs go on, a write to it waits.
CREATE TABLE judge_labels (
    call text NOT NULL,
    judge text NOT NULL,
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    env text NOT NULL CHECK (env IN ('production', 'sandbox')),
    agent text NOT NULL,
    held boolean NOT NULL,
    note text,
    author text NOT NULL,
    labelled_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (call, judge)
);

CREATE INDEX judge_labels_by_org ON judge_labels (org, env, agent);

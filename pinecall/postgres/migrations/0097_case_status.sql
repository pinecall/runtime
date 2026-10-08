-- A case is born at hang-up when a judge broke (evals/dataset.py `kept_at_hangup`), so it waits
-- for a person: `pending` until approved into the nightly or dismissed. The cases that exist were
-- kept by hand and stay `approved`. `broke` is what the panel said, `source_version` the settings
-- version the call ran on, `kept_in_repo` a case written into the repository as a golden, which
-- the nightly then leaves to the file.
--
-- Expanding only: each column has a constant default (no rewrite), the check is read over a
-- table of a few rows per org, and the index is new.

ALTER TABLE eval_cases
    ADD COLUMN status text NOT NULL DEFAULT 'approved'
        CHECK (status IN ('pending', 'approved', 'dismissed')),
    ADD COLUMN broke jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN source_version integer,
    ADD COLUMN kept_in_repo boolean NOT NULL DEFAULT false,
    ADD COLUMN decided_by text,
    ADD COLUMN decided_at timestamptz;

CREATE INDEX eval_cases_pending ON eval_cases (org, agent) WHERE status = 'pending';

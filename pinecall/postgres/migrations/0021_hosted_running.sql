-- What happens to a hosted app while it runs. Stopped: the runner is no longer told to run it, and
-- its releases and token stay. Its logs: the last lines the runner read, when a person asked for
-- them. Metered: when its time serving was last counted, and the time itself, per app and UTC day,
-- which outlives the app because it is what the org is billed for.
ALTER TABLE hosted_apps
    ADD COLUMN stopped_at timestamptz,
    ADD COLUMN stopped_by text NOT NULL DEFAULT '',
    ADD COLUMN logs_asked_at timestamptz,
    ADD COLUMN logs text NOT NULL DEFAULT '',
    ADD COLUMN logs_host text,
    ADD COLUMN logs_at timestamptz,
    ADD COLUMN metered_at timestamptz;

CREATE TABLE hosted_usage (
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    env text NOT NULL CHECK (env IN ('production', 'sandbox')),
    name text NOT NULL,
    day date NOT NULL,
    seconds double precision NOT NULL DEFAULT 0 CHECK (seconds >= 0),
    PRIMARY KEY (org, env, name, day)
);

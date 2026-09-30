-- What a call is, kept when it opens (log/openings.py): its context and the tuned config it runs
-- on, so a gateway that did not open it serves its doors from one read instead of from the
-- worker saying the call again. A config is kept once per org under the hash of its JSON: most
-- calls of an agent run on a handful. An opening goes with its call's erasure; both with the org.
-- New tables: CREATE TABLE locks nothing that exists but `orgs`, in SHARE ROW EXCLUSIVE for each
-- foreign key, for the instant of the statement: reads of orgs go on, a write to it waits.
CREATE TABLE call_configs (
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    hash text NOT NULL,
    config jsonb NOT NULL,
    PRIMARY KEY (org, hash)
);

CREATE TABLE call_openings (
    call text PRIMARY KEY,
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    context jsonb NOT NULL,
    config_hash text NOT NULL,
    opened_at timestamptz NOT NULL DEFAULT now()
);

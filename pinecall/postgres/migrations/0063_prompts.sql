-- Each distinct block of prompt an org's calls were told, kept once under the hash the log's
-- prompt.changed names (tenancy/prompts.py), so a call of thirty days ago has its exact prompt:
-- the knowledge its settings placed, and every block its app set. Written the first time a
-- gateway process meets a text (a call opening with its knowledge, an app's prompt.set), never
-- once per call. Tenant data: the org's foreign key takes it with the org.
--
-- A new table: CREATE TABLE locks nothing that exists but `orgs`, in SHARE ROW EXCLUSIVE for the
-- foreign key, for the instant of the statement: reads of orgs go on, a write to it waits.

CREATE TABLE prompts (
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    hash text NOT NULL,
    text text NOT NULL,
    first_used_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, hash)
);

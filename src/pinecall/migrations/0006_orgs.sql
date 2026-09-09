-- 0006: the organisation. The `fleet` every table keyed on becomes the org, and the org is a row.
--
-- Until here a fleet was a string on a key, on a route and on a token, checked at every door and
-- created nowhere: whatever `keys issue --fleet` typed was a tenant. Now a tenant is a row in
-- `orgs`, and the rows that carried a fleet carry its org instead. Every fleet that ever held a key,
-- a route or a token becomes an org named after itself, so a box that is up today comes through
-- with no manual step: its `default` fleet is the `default` org, and a worker that knocked with a
-- key issued to fleet `default` still knocks with a key issued to org `default`.
--
-- Quotas are a second table, one row per org, because a quota is a different fact from an org:
-- an org exists before anybody limits it, and a self-hosted box never limits it at all. NULL is
-- no limit; the mechanism is here and whoever charges sets the numbers. See docs/decisions/orgs.md.

CREATE TABLE IF NOT EXISTS orgs (
    id          text PRIMARY KEY,
    slug        text NOT NULL UNIQUE,
    name        text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS quotas (
    org               text PRIMARY KEY REFERENCES orgs (id) ON DELETE CASCADE,
    minutes           integer,
    messages          integer,
    agents            integer,
    concurrent_calls  integer,
    set_at            timestamptz NOT NULL DEFAULT now()
);

-- The first org, and the one every default points at.
INSERT INTO orgs (id, slug, name) VALUES ('default', 'default', 'default')
    ON CONFLICT (id) DO NOTHING;

-- Every fleet a box already knows becomes an org whose id, slug and name are the fleet's own word,
-- so nothing that was issued under it has to be reissued.
INSERT INTO orgs (id, slug, name)
    SELECT DISTINCT fleet, fleet, fleet FROM api_keys
    UNION SELECT DISTINCT fleet, fleet, fleet FROM routes
    UNION SELECT DISTINCT fleet, fleet, fleet FROM tokens
    ON CONFLICT (id) DO NOTHING;

-- The fleet was the identity every door checked; the org column beside it was written and never
-- read. From here the org is the identity, and the row says which one the fleet was.
UPDATE api_keys SET org = fleet;
ALTER TABLE api_keys DROP COLUMN fleet;

ALTER TABLE routes RENAME COLUMN fleet TO org;

UPDATE tokens SET org = fleet;
ALTER TABLE tokens DROP COLUMN fleet;

-- Whose log this is. A call's log belongs to the org whose key opened it; an agent's own log to
-- the org that registered the slug, which is what makes a slug one org's and refuses a second.
-- Logs written before this migration are the default org's: a box had one tenant by construction.
ALTER TABLE call_log_head ADD COLUMN IF NOT EXISTS org text;
UPDATE call_log_head SET org = 'default' WHERE org IS NULL;

-- One number across every log, in the order rows were written, so a projection that folds every
-- org's call.summary rows has a cursor to resume from: a seq counts one log and cannot.
ALTER TABLE call_log ADD COLUMN IF NOT EXISTS position bigint GENERATED ALWAYS AS IDENTITY;

-- The projection reads the metered types and nothing else, by position: this is the whole scan.
CREATE INDEX IF NOT EXISTS call_log_metered
    ON call_log (type, position);

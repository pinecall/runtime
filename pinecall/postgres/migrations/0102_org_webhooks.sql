-- Where an org's alerts go (tenancy/webhooks.py): a URL of its own, posted to when a monitor
-- fires, the spend is unusual or a quota runs out, with the secret each post is signed with
-- sealed in the vault. One row per org, under the same row-level security every table with an
-- `org` column has. Expanding only: a new table.

CREATE TABLE org_webhooks (
    org text NOT NULL,
    url text NOT NULL,
    ciphertext text,
    set_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT org_webhooks_pkey PRIMARY KEY (org)
);

ALTER TABLE org_webhooks ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_scoped ON org_webhooks
    USING (coalesce(current_setting('pinecall.org', true), '') = ''
           OR org = current_setting('pinecall.org', true))
    WITH CHECK (coalesce(current_setting('pinecall.org', true), '') = ''
                OR org = current_setting('pinecall.org', true));

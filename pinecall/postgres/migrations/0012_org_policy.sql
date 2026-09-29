-- An org's own compliance settings, one row. Retention first: how many days a sealed call is kept
-- before the nightly run erases it (tenancy/retention.py). No row, or no days, is kept until the
-- org erases it: what a self-hosted box wants unless its operator says otherwise.

CREATE TABLE org_policy (
    org text PRIMARY KEY REFERENCES orgs(id) ON DELETE CASCADE,
    retention_days integer CHECK (retention_days > 0),
    set_by text NOT NULL,
    set_at timestamptz NOT NULL DEFAULT now()
);

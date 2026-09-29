-- Consent and the org's do-not-call list, per world. A row is a fact about a number, never
-- changed: a consent given, or an opt-out. What stands is the newest row, so a person who opted out
-- and later consented again is called again, and the history of both is kept.
-- An erasure of the contact leaves these rows: an opt-out has to outlive the person's data, or the
-- next list the org imports calls them again.

CREATE TABLE contact_consents (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    env text NOT NULL CHECK (env IN ('production', 'sandbox')),
    number text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('express', 'written', 'opt_out')),
    source text NOT NULL,
    text text,
    evidence text,
    given_by text NOT NULL,
    call text,
    given_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX contact_consents_by_number ON contact_consents (org, env, number, given_at DESC, id DESC);

-- Consent on file for a call to any country, not only a +1 number (tenancy/dial_policy.py).
ALTER TABLE org_policy ADD COLUMN consent_everywhere boolean NOT NULL DEFAULT false;

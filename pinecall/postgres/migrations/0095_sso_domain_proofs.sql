-- A domain an org's SSO admits is proven the org's by a TXT record at the domain
-- (tenancy/sso.py): the token here, published as `pinecall-verify=<token>`, read back by the
-- verify door. Discovery offers an org's provider for a verified domain alone, and so does the
-- callback. Every domain already declared is verified here: the orgs that set SSO before this
-- migration are the box's own, and their sign-in keeps working.
-- A new table: CREATE TABLE locks nothing that exists.
CREATE TABLE sso_domain_proofs (
    org text NOT NULL,
    domain text NOT NULL,
    token text NOT NULL,
    asked_at timestamptz NOT NULL DEFAULT now(),
    verified_at timestamptz,
    PRIMARY KEY (org, domain)
);

INSERT INTO sso_domain_proofs (org, domain, token, verified_at)
SELECT org, unnest(domains), 'before-proofs', now() FROM org_sso;

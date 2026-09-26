-- 0030: where an org's people prove who they are — one identity provider per org.
--
-- Until here a person was their email and their password, chosen on this box and argon2id at
-- rest (0014). An org whose people already exist in Google Workspace, Okta or Entra does not
-- want a second password: it wants the box to believe its own IdP. So one row per org holds the
-- OpenID Connect client this gateway is at that IdP, the email domains the org signs in with,
-- and what an address nobody invited becomes — NULL for nothing, which is the default.
--
-- The client secret is a Fernet token under the box's PINECALL_VAULT_KEY, exactly as a provider
-- key (0007) and a carrier (0015) are, and for the same reason: a stolen dump is not a stolen
-- directory. Nothing else here is a secret — an issuer and a client id are public by design, and
-- an operator reading this table has to be able to tell WHICH IdP an org is wired to.
--
-- `required` is the org saying a password opens it no longer. The way back when the IdP stops
-- answering is the operator's (`pinecall-runtime orgs sso <org> --off`) and never the org's: the
-- admin who would turn it off is exactly the person locked out.

CREATE TABLE IF NOT EXISTS org_sso (
    org         text PRIMARY KEY REFERENCES orgs (id) ON DELETE CASCADE,
    issuer      text NOT NULL,
    client_id   text NOT NULL,
    ciphertext  text NOT NULL,
    domains     text[] NOT NULL,
    role        text,
    required    boolean NOT NULL DEFAULT false,
    set_at      timestamptz NOT NULL DEFAULT now()
);

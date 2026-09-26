-- 0007: an org may bring its own provider key. One row per (org, vendor), and no row is managed.
--
-- Until here every call of every tenant ran on the keys of the box: the five ANTHROPIC_API_KEY,
-- SONIOX_API_KEY … variables the process read at startup. That is still the default and still the
-- whole of a self-hosted install. What this table adds is the other half of the same mechanism:
-- a tenant who has an account with a vendor puts their key here, and their calls — and only
-- theirs — run that vendor with it. No row means the box's key, exactly as before.
--
-- The key is never stored in the clear. `ciphertext` is a Fernet token under the box's own
-- PINECALL_VAULT_KEY, which lives in the environment and never in this database, so a stolen dump
-- is not a stolen tenant. Nothing reads a row back but the worker's own door. See
-- docs/decisions/provider-keys.md.

CREATE TABLE IF NOT EXISTS provider_keys (
    org         text NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    vendor      text NOT NULL,
    ciphertext  text NOT NULL,
    set_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org, vendor)
);

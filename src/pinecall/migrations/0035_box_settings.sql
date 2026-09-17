-- 0035: what the operator configured for the BOX, from its own doors and not from a file.
--
-- Until here everything about the box itself was a line of its environment: the mail server it
-- posts letters through (PINECALL_SMTP_URL), and nothing at all for the two things that had no
-- variable — the brand its letters carry, and a "Continue with Google" every org's people may
-- use. A setting that lives in an environment file is changed by whoever can ssh in and restart
-- the gateway; the person who runs a box from /admin is not always that person.
--
-- One row per setting, by name: `brand`, `mail`, `signin.google`. A table of rows and not of
-- columns on purpose — a second box-wide identity provider is one more ROW, and the day a
-- setting is retired its row is deleted and no migration drops a column.
--
-- `value` is what may be read back: a host, a client id, a colour, what came of the last letter.
-- `ciphertext` is the one secret the setting has, when it has one — an SMTP password, an OAuth
-- client secret — as a Fernet token under the box's PINECALL_VAULT_KEY, exactly as a provider
-- key (0007), a carrier (0015), an OIDC client secret (0030) and an org's SMTP password (0034)
-- are: a stolen dump is not a stolen mail account. A setting with no secret keeps NULL there,
-- which is why the brand needs no vault key at all.
--
-- The environment still works, and is what a box with no row falls back to.

CREATE TABLE IF NOT EXISTS box_settings (
    name        text PRIMARY KEY,
    value       jsonb NOT NULL,
    ciphertext  text,
    set_at      timestamptz NOT NULL DEFAULT now()
);

-- 0015: whose numbers reach an org's agents. One carrier per org, its credentials encrypted.
--
-- Until here a number reached the box through the one trunk an operator wired by hand with
-- infra/tools/twilio_trunk.py: the box's Twilio, the box's trunk, `routes add` after. A tenant
-- with numbers of its own — a Twilio account, or a carrier and a PBX that speak SIP — needs the
-- gateway to do that wiring for them, and to act on THEIR account: a trunk pointed at the box,
-- a number attached, a LiveKit inbound trunk of the org's own with the number on it, a route.
--
-- The credentials are a Fernet token under the box's PINECALL_VAULT_KEY, exactly as a provider
-- key is (0007), and for the same reason: a stolen dump is not a stolen carrier account. `kind`
-- says which shape the token opens to; `account` is the one thing a listing may say — the Twilio
-- account SID, or the SIP user — and never the secret.

CREATE TABLE IF NOT EXISTS carriers (
    org         text PRIMARY KEY REFERENCES orgs (id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('twilio', 'sip')),
    account     text NOT NULL,
    ciphertext  text NOT NULL,
    set_at      timestamptz NOT NULL DEFAULT now()
);

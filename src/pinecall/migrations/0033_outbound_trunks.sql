-- 0033: the trunk an org places a call THROUGH, once provisioned.
--
-- A number imported gives the org a way in: its carrier's trunk points at the box and the box's
-- SIP admits it (0015, api/numbers.py). Dialling is the other direction, and it needs a second
-- trunk nobody was keeping: a LiveKit SIP OUTBOUND trunk, holding where to send the INVITE, the
-- numbers it may show as the caller, and the credentials the far side asks for.
--
-- The row is the provisioning's memory, and it exists because two of those facts cannot be read
-- back from anywhere. Twilio never shows a credential's password again once it is created, and
-- LiveKit never shows a trunk's auth_password: a box that forgot them could only repair the trunk
-- by minting a second credential on the tenant's account every time somebody pressed the button.
-- So the password the box minted is sealed here under PINECALL_VAULT_KEY, exactly as a carrier's
-- credentials and a provider key are, and the provisioning is idempotent because of it.
--
-- One per org, both worlds: a trunk is the org's account and its numbers, and which world a call
-- is logged in is the dispatch's business, not the carrier's.

CREATE TABLE IF NOT EXISTS outbound_trunks (
    org         text PRIMARY KEY REFERENCES orgs (id) ON DELETE CASCADE,
    -- twilio or sip: which kind the carrier was when this was provisioned. A carrier replaced
    -- with one of the other kind leaves this row standing and no longer matching, which the
    -- read door says out loud rather than dialling through a trunk for an account nobody holds.
    kind        text NOT NULL CHECK (kind IN ('twilio', 'sip')),
    -- The SFU's own id for the outbound trunk, ST_… — what CreateSIPParticipant is given.
    trunk_id    text NOT NULL,
    -- Where the INVITE goes: <org>.pstn.twilio.com for a Twilio termination, or the peer's own
    -- host. Kept so a repair can tell a trunk that is still pointed right from one that is not.
    address     text NOT NULL,
    -- What the box authenticates as. The username is not a secret and is read back by the console;
    -- the password is one Fernet token over its JSON, and no door ever answers with it.
    username    text,
    ciphertext  text,
    set_at      timestamptz NOT NULL DEFAULT now()
);

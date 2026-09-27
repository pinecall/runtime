-- An org holds many carrier accounts (Twilio accounts, SIP peers, WhatsApp numbers at Meta), one
-- row each, keyed by the account's own id. A route names the account its number lives in, or
-- none: a number the org hooked itself, or one the box bought. A number the org hooked itself
-- keeps the networks the box admits it from on its own row.

ALTER TABLE carriers DROP CONSTRAINT carriers_pkey;
ALTER TABLE carriers ADD CONSTRAINT carriers_pkey PRIMARY KEY (org, account);
ALTER TABLE carriers ADD COLUMN label text NOT NULL DEFAULT '';
ALTER TABLE carriers DROP CONSTRAINT carriers_kind_check;
ALTER TABLE carriers ADD CONSTRAINT carriers_kind_check
    CHECK (kind = ANY (ARRAY['twilio'::text, 'sip'::text, 'whatsapp'::text]));

ALTER TABLE routes ADD COLUMN account text;
ALTER TABLE routes ADD COLUMN networks text[] NOT NULL DEFAULT '{}';
-- Forgetting an account leaves its numbers routed until each is let go.
ALTER TABLE routes ADD CONSTRAINT routes_account_fkey FOREIGN KEY (org, account)
    REFERENCES carriers (org, account) ON DELETE SET NULL (account);

-- The SFU keeps no outbound trunk: a leg is dialled with its trunk inline.
DROP TABLE outbound_trunks;

-- Which countries a dial reaches is the carrier account's setting; nothing ever read this.
ALTER TABLE dial_policy DROP COLUMN countries;

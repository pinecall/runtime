-- 0034: the mail server an org sends its own letters through, and what came of the last one.
--
-- Until here this box sent no email at all: an invitation and a password reset were a one-use
-- link an admin copied out of an answer and passed on by hand. A gateway that can post them is
-- one setting away (PINECALL_SMTP_URL), and the transport is generic SMTP — Amazon SES,
-- Postmark, Mailgun and a mail server of one's own all speak it, so there is no vendor here.
--
-- An org that wants its people's letters to come from its own domain wires its own account, and
-- that account wins over the box's. The SMTP password is a Fernet token under the box's
-- PINECALL_VAULT_KEY, exactly as a provider key (0007), a carrier (0015) and an OIDC client
-- secret (0030) are: a stolen dump is not a stolen mail account. Nothing else here is a secret —
-- a host, a port and a username are what an admin reads back to see what they wired.
--
-- `verified_at` and `last_error` are what came of the last letter posted through it, because the
-- send happens after the door has answered and there is nowhere else for an admin to look. One
-- of the two is always null: a letter that is taken clears the error and dates the row, and one
-- that is refused keeps the date of the last that worked beside the sentence the server said.
-- Both are reset by a PUT, since what a server said about the old password is not news about a
-- new one.

CREATE TABLE IF NOT EXISTS org_mail (
    org         text PRIMARY KEY REFERENCES orgs (id) ON DELETE CASCADE,
    host        text NOT NULL,
    port        integer NOT NULL,
    security    text NOT NULL,
    -- Empty for a relay that asks for no credentials, which is a mail server on the same box.
    username    text NOT NULL,
    ciphertext  text NOT NULL,
    sender      text NOT NULL,
    verified_at timestamptz,
    last_error  text,
    set_at      timestamptz NOT NULL DEFAULT now()
);

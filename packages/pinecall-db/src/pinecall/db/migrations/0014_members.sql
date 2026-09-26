-- 0014: the people of an org, and the invitations that make them.
--
-- Until here a tenant was an org and a key, and a key was a machine's: the worker's, the app's,
-- a laptop's. A person reaches the console with a key of their own, minted for them at login
-- with the scopes their role presets (types/member.py) — so a person is a row, with a password
-- of their own invention hashed as such (argon2id, auth/passwords.py), never a shared org key
-- pasted into a browser. The role is on the row and NOT on the key: a role re-cut tomorrow
-- changes the next key issued and not a door, because the doors read scopes.
--
-- A member is made by an invitation: a one-use token in a link, kept here as its sha256 exactly
-- as a key is, dead in a week on its own. Accepting it sets the password and makes the row
-- active. A disabled member keeps their row — the log names them — and cannot log in.

CREATE TABLE IF NOT EXISTS members (
    id             text PRIMARY KEY,
    org            text NOT NULL REFERENCES orgs (id) ON DELETE CASCADE,
    email          text NOT NULL,
    name           text NOT NULL,
    role           text NOT NULL
        CHECK (role IN ('qa', 'supervisor', 'manager', 'admin', 'developer')),
    -- Which of the org's agents this person works on. Empty is every one of them.
    agents         text[] NOT NULL DEFAULT '{}',
    status         text NOT NULL
        CHECK (status IN ('invited', 'active', 'disabled')),
    -- NULL until the invitation is accepted: an invited member has no password yet.
    password_hash  text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org, email)
);

CREATE TABLE IF NOT EXISTS invitations (
    token_hash  text PRIMARY KEY,
    member      text NOT NULL REFERENCES members (id) ON DELETE CASCADE,
    expires_at  timestamptz NOT NULL,
    -- Accepted, or replaced by a newer invitation: either way this token opens nothing more.
    spent_at    timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

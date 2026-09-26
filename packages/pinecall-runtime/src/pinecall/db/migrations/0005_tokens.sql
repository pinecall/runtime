-- 0005: the ledger of the call tokens POST /v1/tokens minted, so each one opens a call once.
--
-- One row per token, keyed by the call it was minted for: the room's name IS the call id, and a
-- token names exactly one room, so there is no second id to invent. The dispatch spends the row
-- (spent_at) the moment the worker opens the call; a second join with the same token creates a
-- second dispatch, finds the row spent, and is refused. LiveKit has no revocation self-hosted and
-- no notion of "once", so this table is the one semantics the standard token endpoint lacks — see
-- docs/decisions/tokens.md. A spent row is kept, never deleted: it is the record of who opened
-- what, and an expired one has already been refused by the media plane on the signature's exp.

CREATE TABLE IF NOT EXISTS tokens (
    call        text PRIMARY KEY,
    org         text NOT NULL,
    fleet       text NOT NULL,
    agent       text NOT NULL,
    scope       text NOT NULL,
    minted_at   timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL,
    spent_at    timestamptz
);

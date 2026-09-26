-- 0002: the API keys a door verifies a bearer against.
--
-- One row per key, found by the hash and never by the key: what the app sent is fingerprinted in
-- the process and the plaintext never reaches Postgres. A revoked key is kept, not deleted — the
-- logs it wrote name it, and a row that vanishes makes those unreadable — so revocation is a
-- timestamp and the lookup is the one that filters on it.

CREATE TABLE IF NOT EXISTS api_keys (
    id          text PRIMARY KEY,
    hash        text NOT NULL UNIQUE,
    org         text NOT NULL,
    fleet       text NOT NULL,
    label       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    revoked_at  timestamptz
);

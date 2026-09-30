-- Words spent once, kept here so a word minted on one gateway is spent on any (tenancy/words.py):
-- a sign-in code, a terminal waiting for a browser, a provider's handshake, a sign-up waiting for
-- its six digits. Before, each gateway held its own in memory. A word is kept as its sha256 and
-- its value sealed under the vault: a copy of this table opens nothing, and a pairing's key is
-- never in the clear. Swept as words are minted.
-- A new table: CREATE TABLE locks nothing that exists.
CREATE TABLE one_use_words (
    word_hash bytea PRIMARY KEY,
    kind text NOT NULL,
    sealed text NOT NULL,
    expires_at double precision NOT NULL,
    attempts integer NOT NULL DEFAULT 0
);

CREATE INDEX one_use_words_by_end ON one_use_words (expires_at);

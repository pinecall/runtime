-- The four-digit codes a caller keys to tie their call to a page (tenancy/codes.py), kept here so
-- any gateway issues, claims and answers them: before, each gateway held its own in memory and a
-- code issued on one was unknown to another. The agent's log keeps code.issued and code.claimed
-- as the record; this table is what is live. A code is claimed by one call, in one statement.
-- A new table: CREATE TABLE locks nothing that exists.
CREATE TABLE caller_codes (
    agent text NOT NULL,
    code text NOT NULL,
    env text NOT NULL CHECK (env IN ('production', 'sandbox')),
    log text NOT NULL CHECK (log IN ('public', 'tenant')),
    expires_at double precision NOT NULL,
    claimed text,
    PRIMARY KEY (agent, code)
);

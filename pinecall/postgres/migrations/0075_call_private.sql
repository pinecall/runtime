-- What is private is sealed when written. An entry whose agent declared some of its values
-- private (a tool's `pii` arguments, a state field declared `pii`) is kept in call_log with
-- those values masked, and the values themselves are kept here, sealed under PINECALL_VAULT_KEY,
-- one row per entry, keyed by the entry's log and seq (log/private.py). They are read only where
-- a call is taken up: an app taking it over, a written call after a restart. `vault rotate`
-- re-seals the column; an erasure deletes the rows of the logs it erases (tenancy/erasure.py).
-- A new table with no foreign key: CREATE TABLE takes no lock on any table that exists, so
-- appends and reads go on while it runs.
CREATE TABLE call_private (
    log text NOT NULL,
    seq bigint NOT NULL,
    sealed text NOT NULL,
    PRIMARY KEY (log, seq)
);

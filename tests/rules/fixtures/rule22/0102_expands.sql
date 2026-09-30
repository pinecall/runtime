-- What the release before it lives with: new tables, columns it need not write, relaxed rules.
CREATE TABLE notes (id bigint PRIMARY KEY, body text NOT NULL);

ALTER TABLE call_log_head
    ADD COLUMN spent numeric NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS number bigint GENERATED ALWAYS AS IDENTITY,
    ADD COLUMN said text,
    ALTER COLUMN seen DROP NOT NULL,
    ALTER COLUMN kept SET DEFAULT now(),
    DROP CONSTRAINT old_check,
    ADD CONSTRAINT said_short CHECK (length(said) < 100);

DROP INDEX call_log_head_seen;
ALTER TABLE call_facts RENAME CONSTRAINT old TO new;

-- A swap: the table renamed away becomes a partition of a new one under its name.
ALTER TABLE entries RENAME TO entries_before;
CREATE TABLE entries (id bigint NOT NULL, at double precision NOT NULL) PARTITION BY RANGE (at);

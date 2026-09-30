-- call_log becomes a table partitioned by range on ts, a partition per UTC day: retention drops a
-- day instead of deleting rows, and the rows written today sit in a table and indexes the size of
-- a day. The table as it was becomes the partition of everything before the bound 0025 set (the
-- start of the day after it ran), keeping its rows, its key (0024's) and its index; the parent
-- takes the name, the columns, the CHECK, the identity (numbering on from the last position
-- given) and the triggers, so a release before this one reads and writes call_log as it did. A
-- default partition catches a row no day holds, so a missing day never refuses an append;
-- `pinecall-runtime retention run` makes the days ahead each night and doctor names a row in the
-- default.
--
-- Locks and time: LOCK TABLE takes ACCESS EXCLUSIVE on call_log, held to the commit, so appends
-- and reads of the log wait; everything under it changes the catalog and reads no row (the
-- partition's bound is proved by 0026's constraint, its key and index exist), so with N rows it
-- holds them for tens of milliseconds, measured at 2 million rows. The runner gives up after 1 s
-- of waiting for the lock, before anything changed.

LOCK TABLE call_log IN ACCESS EXCLUSIVE MODE;

DROP TRIGGER call_log_refuses_delete ON call_log;
DROP TRIGGER call_log_refuses_update ON call_log;
DROP TRIGGER call_log_refuses_a_sealed_log ON call_log;
DROP TRIGGER usage_of_summaries_written ON call_log;
DROP TRIGGER usage_of_summaries_erased ON call_log;

-- The key becomes 0024's index, which holds the partition key; the rows are not read.
ALTER TABLE call_log DROP CONSTRAINT call_log_one_row_per_seq;
ALTER TABLE call_log ADD CONSTRAINT call_log_before_one_row_per_seq
    PRIMARY KEY USING INDEX call_log_by_seq_and_day;

-- The last position given, kept for this transaction alone: the parent numbers on from it.
SELECT set_config(
    'pinecall.last_position',
    coalesce(pg_sequence_last_value('call_log_position_seq'::regclass), 0)::text,
    true);

-- A partition may not number its rows itself: the parent does, from where the table left off.
ALTER TABLE call_log ALTER COLUMN "position" DROP IDENTITY;
ALTER TABLE call_log RENAME TO call_log_before_the_days;

CREATE TABLE call_log (
    call text,
    seq bigint NOT NULL,
    ts double precision NOT NULL,
    agent text NOT NULL,
    type text NOT NULL,
    ephemeral boolean NOT NULL,
    data jsonb NOT NULL,
    log text GENERATED ALWAYS AS (COALESCE(call, ('@'::text || agent))) STORED NOT NULL,
    "position" bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
    CONSTRAINT call_log_call_never_opens_with_at CHECK (((call IS NULL) OR ("left"(call, 1) <> '@'::text)))
) PARTITION BY RANGE (ts);

-- The bound is 0025's, read back from its constraint; the days from it on are made here, a week.
DO $$
DECLARE
    bound double precision := (
        SELECT (regexp_match(pg_get_constraintdef(oid), '\(ts < \(?''?(-?[0-9.e+]+)'))[1]::float8
        FROM pg_constraint
        WHERE conname = 'call_log_before_the_days'
          AND conrelid = 'call_log_before_the_days'::regclass);
    day double precision;
BEGIN
    EXECUTE format(
        'ALTER TABLE call_log ATTACH PARTITION call_log_before_the_days '
        'FOR VALUES FROM (MINVALUE) TO (%s)', bound);
    EXECUTE format(
        'ALTER TABLE call_log ALTER COLUMN "position" RESTART WITH %s',
        current_setting('pinecall.last_position')::bigint + 1);
    FOR n IN 0..6 LOOP
        day := bound + n * 86400;
        EXECUTE format(
            'CREATE TABLE %I PARTITION OF call_log FOR VALUES FROM (%s) TO (%s)',
            'call_log_' || to_char(to_timestamp(day) AT TIME ZONE 'UTC', 'YYYYMMDD'),
            day, day + 86400);
    END LOOP;
END
$$;

CREATE TABLE call_log_default PARTITION OF call_log DEFAULT;

ALTER TABLE call_log ADD CONSTRAINT call_log_one_row_per_seq_and_day PRIMARY KEY (log, seq, ts);
CREATE INDEX call_log_metered_by_day ON call_log (type, "position");

CREATE TRIGGER call_log_refuses_delete BEFORE DELETE ON call_log
    FOR EACH STATEMENT EXECUTE FUNCTION call_log_refuses_the_statement();
CREATE TRIGGER call_log_refuses_update BEFORE UPDATE ON call_log
    FOR EACH STATEMENT EXECUTE FUNCTION call_log_refuses_the_statement();
CREATE TRIGGER call_log_refuses_a_sealed_log AFTER INSERT ON call_log
    REFERENCING NEW TABLE AS written
    FOR EACH STATEMENT EXECUTE FUNCTION call_log_refuses_a_sealed_log();
CREATE TRIGGER usage_of_summaries_written AFTER INSERT ON call_log
    REFERENCING NEW TABLE AS summaries
    FOR EACH STATEMENT EXECUTE FUNCTION usage_of_summaries_written();
CREATE TRIGGER usage_of_summaries_erased AFTER DELETE ON call_log
    REFERENCING OLD TABLE AS summaries
    FOR EACH STATEMENT EXECUTE FUNCTION usage_of_summaries_erased();

-- The bound between the log as it is and its days: every entry written until the swap is before
-- the start of the day after tomorrow (UTC), which a CHECK says, NOT VALID: adding it takes an
-- ACCESS EXCLUSIVE lock for the instant the catalog changes and reads no row. 0026 validates it
-- without holding writes; 0027 reads the bound back, so the old table becomes the partition of
-- everything before it and is never scanned under a lock that stops appends.
DO $$
DECLARE
    bound double precision := extract(epoch FROM date_trunc('day', now() AT TIME ZONE 'UTC')
                                                 + interval '2 days');
BEGIN
    EXECUTE format(
        'ALTER TABLE call_log ADD CONSTRAINT call_log_before_the_days CHECK (ts < %s) NOT VALID',
        bound);
END
$$;

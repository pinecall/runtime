-- The bound of 0025 proved on every row: VALIDATE takes SHARE UPDATE EXCLUSIVE, so appends go on
-- while it reads the table once (with N rows, the time of one sequential pass). A row past the
-- bound would fail it here, before anything is swapped.
ALTER TABLE call_log VALIDATE CONSTRAINT call_log_before_the_days;

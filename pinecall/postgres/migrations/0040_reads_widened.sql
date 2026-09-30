-- Every read is on the record: a server's key reading a call, a seat that listens to or
-- supervises a live call, an export of the org's world, and a read of what memory keeps of a
-- contact join the log, beside a person's and the operator's reads of a call, a recording and a
-- number.
--
-- Lock: ACCESS EXCLUSIVE on `reads` for two catalog changes and no scan, milliseconds under the
-- runner's lock_timeout. NOT VALID skips checking the rows already there, which the narrower
-- check admitted; every row written from now on is checked. A VALIDATE would hold the same lock
-- over a scan of the table, and every read of a call waits on its row here.

ALTER TABLE reads
    DROP CONSTRAINT reads_what_check,
    ADD CONSTRAINT reads_what_check
        CHECK (what IN ('log', 'recording', 'traceback', 'listen', 'supervise', 'export', 'memory'))
        NOT VALID;

-- The first of four that make call_log a table partitioned by day (0024 to 0027). A partitioned
-- table's key holds the partition key, so the log's key becomes (log, seq, ts), built here beside
-- the old one. Not CONCURRENTLY: that waits for every transaction open in the database, the
-- migration runner's own lock holder among them. CREATE INDEX takes SHARE on call_log: reads go on,
-- appends wait for one pass over the table and a sort of its keys (with N rows, about a second per
-- two million on the box's disk; production today holds far fewer). The swap (0027) makes it the
-- key of the table's first partition.
CREATE UNIQUE INDEX call_log_by_seq_and_day ON call_log (log, seq, ts);

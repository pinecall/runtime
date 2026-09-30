-- A worker writes its call's entries in batches and retries a batch whose answer it lost. The head
-- row counts what it took from that writer, so a retry is recognised and answered with the seqs it
-- was given instead of being written again under new ones. The gateway's own writes move neither.

ALTER TABLE call_log_head
    ADD COLUMN written bigint NOT NULL DEFAULT 0,
    ADD COLUMN written_seq bigint;

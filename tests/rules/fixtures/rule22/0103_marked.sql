-- pinecall:contracts call_log_head.written unread since 0.1.1
-- pinecall:contracts call_log_head.spent unread since 0.1.3
-- One contraction a released version stopped reading, one a release not out yet.
ALTER TABLE call_log_head DROP COLUMN written, DROP COLUMN IF EXISTS spent;

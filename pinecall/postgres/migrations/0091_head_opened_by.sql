-- Who opened a call's log (log/store.py Claim): the fleet's worker, an org's own worker (an app
-- key), or the gateway itself for a written call. The worker doors of a call the fleet opened
-- take the fleet's key alone; a written call's take nobody's. NULL is a call opened before this
-- column, served as before.
-- New column, nullable: nothing that exists stops reading.
ALTER TABLE call_log_head ADD COLUMN opened_by text;

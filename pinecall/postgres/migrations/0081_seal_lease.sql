-- The seal's lease: a call is sealed by one gateway, whichever the worker's seal reached first.
-- The gateway that takes the lease (log/store.py `lease_seal`) prices, remembers, judges and seals;
-- one that finds it taken waits for the head to say sealed. A lease runs out on its own, so a
-- gateway that died mid-seal leaves the call to the next knock.
-- ADD COLUMN with no default and no NOT NULL changes the catalog alone: ACCESS EXCLUSIVE on
-- call_log_head for the instant of the statement, no rewrite; an append waits that instant.
ALTER TABLE call_log_head ADD COLUMN sealing_until timestamptz;

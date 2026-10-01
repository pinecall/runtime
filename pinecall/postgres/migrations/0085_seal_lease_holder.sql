-- Who holds the seal's lease: the gateway process that took it (log/store.py `lease_seal`), so
-- only it renews the lease while it seals, and only it gives the lease back after a seal that
-- broke. The lease is short (seal.py LEASED_S) and renewed: a gateway that dies mid-seal lets the
-- call go in seconds, and a knock waiting on it takes the seal over; a gateway that lost the race
-- cannot keep the winner's lease alive by renewing it.
-- ADD COLUMN with no default and no NOT NULL changes the catalog alone: ACCESS EXCLUSIVE on
-- call_log_head for the instant of the statement, no rewrite; an append waits that instant.
ALTER TABLE call_log_head ADD COLUMN sealing_by text;

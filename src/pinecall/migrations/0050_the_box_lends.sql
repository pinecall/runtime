-- 0050: which of the box's vendor keys an org's calls may run on where it brought none of its own.

-- NULL lends every one, which is what every org nobody limited has and what a self-hosted box
-- runs on — no row, or this column left NULL. An empty array lends nothing: the org runs only on
-- the keys it brought. Each element is a vendor (`deepgram`: every model of it) or `vendor/model`
-- (`anthropic/claude-haiku-4-5`: that model and its dated snapshots, read as a prefix). The
-- column is a set the operator API replaces whole with the rest of the quotas row; what an entry
-- means is src/pinecall/providers/lending.py, never a price.

ALTER TABLE quotas ADD COLUMN IF NOT EXISTS lends text[];

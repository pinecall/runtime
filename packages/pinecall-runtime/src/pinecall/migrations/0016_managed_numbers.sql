-- 0016: numbers bought for an org on the box's own carrier account, and the cap on them.
--
-- A tenant imports its own numbers (0015) and they count against nothing: they are theirs. A
-- number the box BUYS for an org — from Pinecall's Twilio, on a plan — is a stock the plan caps,
-- exactly as memory facts and knowledge chunks are (0011): NULL is no limit, 0 is none at all,
-- a number is a cap. Which routes are such numbers is a flag on the row, because a number IS a
-- route and a second table would be a second truth about the same door.

ALTER TABLE quotas ADD COLUMN IF NOT EXISTS numbers integer;

ALTER TABLE routes ADD COLUMN IF NOT EXISTS managed boolean NOT NULL DEFAULT false;

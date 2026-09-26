-- 0019: how many people an org may seat. The last quota that was a mechanism with no number.
--
-- 0014 made a person a row and left the count open: an admin could invite a thousand, and on a
-- box that is right — the people of a tenant are the tenant's business. On a gateway that charges
-- per seat it is the one thing nobody could cap, so the mechanism goes here with the other six
-- and whoever charges sets the number, exactly as `numbers` (0016) and the two stocks (0011) do.
--
-- A seat is held by a member who is `invited` or `active`: an invitation sent is a seat taken, or
-- an org at its limit could invite forever and seat everybody the moment they accepted. A
-- `disabled` member keeps their row — the log names them — and holds no seat, which is what makes
-- disabling somebody the way to free one.

ALTER TABLE quotas ADD COLUMN IF NOT EXISTS seats integer;

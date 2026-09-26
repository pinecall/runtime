-- 0048: an address is a person's once somebody other than an admin proved it.
--
-- A person is their email on this box and has one password (auth/members.py), and an invitation's
-- link buys that password. Nothing recorded WHO could have held the link: one an admin was handed
-- in the answer proves nothing about the address, while one that travelled by mail alone reached
-- the inbox, and an identity provider's id_token names the address outright. So whoever chose an
-- address's password first — through a link of their own org's, or a sign-up — was seated wherever
-- that address was invited next, and their invited rows were seated at their first login.
--
-- `members.verified_at` is when a row's address was proved: a vouched link accepted, a provider's
-- word, the box's operator issuing the invitation (the operator knows who they are seating).
-- `invitations.vouched` says whether accepting THIS link proves the address — false for a link the
-- answer handed to an admin. A person is seated in a second org without a link, and an invited
-- row of theirs seated at login, only when some row of theirs is verified.
--
-- Nobody is grandfathered: a row active today was made through a handed link as often as not,
-- and marking it verified would keep the very hole this closes open for everybody already here.
-- What it costs an existing person of two orgs is one invitation by mail, or one from the
-- operator, which is what verifies them. Both are nullable/defaulted, so neither ALTER rewrites
-- a row or holds a lock worth naming.

ALTER TABLE members ADD COLUMN IF NOT EXISTS verified_at timestamptz;

ALTER TABLE invitations ADD COLUMN IF NOT EXISTS vouched boolean NOT NULL DEFAULT false;

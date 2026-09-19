-- 0039: a person opens production because an admin said so, not because a key was minted there.
--
-- Until here a person held one key per world, and the world a request ran in was the key's: a
-- `pinecall login` minted two, and production was reached with the second. Now a person holds ONE
-- key, the request names its world (the `pinecall-env` header), and the gateway lets it into
-- production only when the member's row says `production` — read at every request, so taking it
-- away closes the door on the next one. An admin always opens production — the org's owner must
-- be able to reach what answers its phone — and that is the ROLE's, so no row is marked here: a
-- column set for every admin would stay set on the day one of them stopped being one.
--
-- A person's key no longer carries a world: every one of them becomes a sandbox key, which is
-- what a request that names no world runs in. A key that names nobody (a server's) keeps its
-- world, and so does an operator's visiting key. `created_by` says who made a server's token and
-- stays when that person leaves; `last_used_at` is written when a key opens the app socket or
-- asks /v1/whoami, the two moments a token list needs.

ALTER TABLE members ADD COLUMN IF NOT EXISTS production boolean NOT NULL DEFAULT false;

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS created_by text;

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS last_used_at timestamptz;

UPDATE api_keys
   SET env = 'sandbox'
 WHERE subject IS NOT NULL AND subject NOT LIKE 'operator:%' AND env <> 'sandbox';

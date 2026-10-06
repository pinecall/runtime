-- The key a key was copied from (tenancy/signin.py sign_in_with_code: a login code a key minted
-- signs a browser in with a copy of it). Revoking a key revokes every copy made of it, and every
-- copy of those: a leaked key can no longer outlive its revocation through a copy.
-- New column, nullable: nothing that exists stops reading.
ALTER TABLE api_keys ADD COLUMN parent text;
CREATE INDEX api_keys_parent ON api_keys (parent) WHERE parent IS NOT NULL;

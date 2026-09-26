-- 0049: a key may stop opening anything at a moment written on its row.
--
-- The sandbox became an instance of its own, and production is who says a person is a member: the
-- sandbox mirrors the row when the person signs in there, and mints a key of its own. A member
-- production disables later has nothing to tell the sandbox — no instance calls the other back —
-- so the sandbox's person keys live a day (auth/persons.py): past it the key is refused, the
-- console goes back to production for a new code, and a re-login re-reads the member there and
-- refuses a disabled one. A key that expired reads exactly as a revoked one: the same 401, and
-- nothing said about why.
--
-- NULL is never, which is every key that exists today and every key production mints: its person
-- keys and every server token. Nullable with no default, so the ALTER rewrites no row.

ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS expires_at timestamptz;

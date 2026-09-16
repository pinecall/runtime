-- 0024: a call's head row says which corner it was opened in.
--
-- The head row carried the org (0006) and nothing narrower, so a list of an org's calls was every
-- world's and every developer's at once: a developer's sandbox test call sat beside the
-- telephone's production call, and beside a colleague's (2026-09-16, two developers of one
-- tenant looking at one Sessions screen). Both columns are what the registry already keys an
-- agent by — (env, holder, slug), 0013 — and what memory and knowledge carry (0018, 0021).
--
-- Nullable, and the runtime writes both on every call from here (OWNED, log/store/postgres.py).
-- Every row already here is production's and the org's own: production had one corner and the
-- sandbox's test calls cannot be told from the head row, so they read as the org's — a handful of
-- one day's test calls, listed once beside the real ones and never again. The org's own corner
-- is the empty string and not NULL, as 0021 says: it is part of a key, and NULL matches nothing.
-- An agent's own log (call IS NULL) keeps no corner: one log per slug, whatever the world.

ALTER TABLE call_log_head ADD COLUMN IF NOT EXISTS env text;
ALTER TABLE call_log_head ADD COLUMN IF NOT EXISTS holder text;

UPDATE call_log_head SET env = 'production', holder = ''
 WHERE call IS NOT NULL AND env IS NULL;

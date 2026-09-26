-- 0013: the key knows where and who. Which of two worlds it opens, what it may do there, and
-- whose it is; and the routes an operator typed are one world's too.
--
-- Until here a key was `org · label`: whoever held one held every door of the org, and the
-- gateway had one table of agents and one of numbers. A tenant writing an agent on a laptop and
-- running the same agent on a box needs two worlds on one gateway that never see each other, so
-- a key is issued into one — production or development — and the registry and the routes are
-- namespaced by it. Every key issued before this migration is production's, with every scope: it
-- is the key a box runs on today, and nothing it does may change under it.
--
-- The scopes are the doors as they are grouped, and the closed set is spelled once in the
-- runtime (types/key.py); the literal below is checked against it by a test, so the two cannot
-- drift. The defaults are backfills and nothing more: they are dropped once the rows have them,
-- because from here the runtime writes every column and no policy lives in the table.

ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS env text NOT NULL DEFAULT 'production'
        CHECK (env IN ('production', 'development')),
    ADD COLUMN IF NOT EXISTS scopes text[] NOT NULL DEFAULT ARRAY[
        'app', 'calls', 'talk', 'supervise', 'pipeline', 'knowledge',
        'memory', 'evals', 'numbers', 'keys', 'team', 'usage'
    ],
    -- Whose key it is when it is a person's: the member it was minted for, and their name. An
    -- org's own key — the worker's, the app's — names nobody, and the label says what it is for.
    ADD COLUMN IF NOT EXISTS subject text,
    ADD COLUMN IF NOT EXISTS name text;

ALTER TABLE api_keys
    ALTER COLUMN env DROP DEFAULT,
    ALTER COLUMN scopes DROP DEFAULT;

-- A number an operator typed answers in one world. The row is still the door — (org, number) —
-- so moving a number between worlds is the same UPDATE as moving it between agents.
ALTER TABLE routes
    ADD COLUMN IF NOT EXISTS env text NOT NULL DEFAULT 'production'
        CHECK (env IN ('production', 'development'));

ALTER TABLE routes
    ALTER COLUMN env DROP DEFAULT;

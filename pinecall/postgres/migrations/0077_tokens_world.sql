-- A web call is opened only where its token was minted: the org, the agent and now the world.
-- The world a room token was minted in is kept with it, and the open compares it with the world
-- the worker names (tenancy/tokens.py spend). A token minted before this has none, and is held to
-- its org and agent alone; it expires within minutes of its mint in any case.
-- ADD COLUMN with no default and no NOT NULL changes the catalog alone: ACCESS EXCLUSIVE on tokens
-- for the instant of the statement, no rewrite; a mint or an open waits that instant.
ALTER TABLE tokens ADD COLUMN env text;

-- How a number's row was written, so the org and the operator can tell whom to ask when it stops
-- ringing: bought by the box, imported from an account of the org, hooked by the org itself
-- (pointed at the box from its own carrier), or typed by the box's operator.
-- The default is the operator's: a release before this one writes rows only through
-- POST /v1/ops/routes or an import, and an import written by it during the deploy reads `typed`
-- until the org imports it again.
ALTER TABLE routes ADD COLUMN origin text NOT NULL DEFAULT 'typed'
    CHECK (origin IN ('bought', 'imported', 'hooked', 'typed'));

-- The rows already written, from what each one kept: a row with no account, no networks and not
-- bought was typed (every such row on the box predates the cutover of 2026-09-29).
UPDATE routes SET origin = CASE
    WHEN managed THEN 'bought'
    WHEN account IS NOT NULL THEN 'imported'
    WHEN cardinality(networks) > 0 THEN 'hooked'
    ELSE 'typed'
END;

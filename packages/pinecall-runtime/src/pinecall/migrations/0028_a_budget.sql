-- 0028: what an org's calls may cost in a month.
--
-- A quota like the others (0006, 0011, 0019), set by whoever runs the box, NULL for no limit — and
-- the one of them nothing is refused over: whole euros a calendar month, every world of the org
-- together, read beside what was spent (GET /v1/insights). Whoever charges decides what reaching
-- it means; the runtime only counts.

ALTER TABLE quotas ADD COLUMN IF NOT EXISTS budget_eur integer;

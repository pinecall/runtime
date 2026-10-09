-- A call's tools, counted at its seal beside its verdicts (log/drift.py fold): how many ran and
-- how many answered an error, so a day's tool failures are read off the fold and not off the log.
-- Expanding only: two columns with a constant default, no rewrite.

ALTER TABLE drift_calls
    ADD COLUMN tools_ran integer NOT NULL DEFAULT 0,
    ADD COLUMN tools_failed integer NOT NULL DEFAULT 0;

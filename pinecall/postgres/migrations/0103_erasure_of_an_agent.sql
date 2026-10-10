-- An agent of an org erased whole: its logs, its settings, its callers, judges, monitors, routes,
-- and every count of its calls. The trail says so with what = 'agent' and the slug as its subject.
ALTER TABLE erasures DROP CONSTRAINT erasures_what_check;
ALTER TABLE erasures ADD CONSTRAINT erasures_what_check
    CHECK (what IN ('call', 'contact', 'org', 'agent'));

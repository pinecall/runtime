-- org_telemetry (0098) came after row-level security was switched on (0096): it gets the same
-- policy every table with an `org` column has, so a tenant's connection sees its own row alone.
-- ENABLE and CREATE POLICY take a brief ACCESS EXCLUSIVE lock on a table of one row per org.

ALTER TABLE org_telemetry ENABLE ROW LEVEL SECURITY;
CREATE POLICY org_scoped ON org_telemetry
    USING (coalesce(current_setting('pinecall.org', true), '') = ''
           OR org = current_setting('pinecall.org', true))
    WITH CHECK (coalesce(current_setting('pinecall.org', true), '') = ''
                OR org = current_setting('pinecall.org', true));

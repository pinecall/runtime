-- Row-level security: a connection that names an org (`pinecall.org`, postgres/pool.py, set by
-- the gateway for a tenant's request) sees and writes that org's rows alone, whatever a query
-- forgot to say; one that names none is the box's, as the operator's doors, the loops and the
-- nightly jobs are. Every table with an `org` column, and `orgs` by its id. The owner (the
-- migrations, the retention) is not held to it, as Postgres never holds a table's owner.
-- A log's head is made before its call names an org (the seal's lease, a seal), so a head with no
-- org yet is anybody's to see and write: it holds an id and its flags, nothing of a tenant's.
-- ENABLE takes a brief ACCESS EXCLUSIVE lock per table, CREATE POLICY the same; no row is read.
DO $$
DECLARE
    found record;
    scoped text;
BEGIN
    FOR found IN
        SELECT class.relname AS name, CASE WHEN class.relname = 'orgs' THEN 'id' ELSE 'org' END AS col
        FROM pg_class AS class
        JOIN pg_namespace AS space ON space.oid = class.relnamespace
        WHERE space.nspname = current_schema()
          AND class.relkind IN ('r', 'p')
          AND NOT class.relispartition
          AND (class.relname = 'orgs' OR EXISTS (
              SELECT 1 FROM pg_attribute AS attr
              WHERE attr.attrelid = class.oid AND attr.attname = 'org' AND NOT attr.attisdropped))
    LOOP
        scoped := format(
            'coalesce(current_setting(''pinecall.org'', true), '''') = '''' '
            'OR %I = current_setting(''pinecall.org'', true)',
            found.col
        );
        IF found.name = 'call_log_head' THEN
            scoped := 'org IS NULL OR ' || scoped;
        END IF;
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', found.name);
        EXECUTE format(
            'CREATE POLICY org_scoped ON %I USING (%s) WITH CHECK (%s)', found.name, scoped, scoped
        );
    END LOOP;
END $$;

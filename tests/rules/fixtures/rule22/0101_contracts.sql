-- Every way a migration contracts what the release before it reads, and none marked.
DROP TABLE IF EXISTS calls, public.old_notes CASCADE;

ALTER TABLE call_log_head
    DROP COLUMN written,
    ADD COLUMN note text NOT NULL,
    RENAME COLUMN kept TO held_on,
    ALTER COLUMN seen SET NOT NULL,
    ADD CONSTRAINT one_note CHECK (note <> ''),
    DROP CONSTRAINT old_check;

ALTER TABLE IF EXISTS ONLY public.call_facts RENAME outcome TO ending;

ALTER TABLE call_facts RENAME TO facts;

CREATE FUNCTION kept() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    DROP TABLE inside_a_body;
    RETURN NEW;
END;
$$;

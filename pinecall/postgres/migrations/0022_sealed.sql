-- A sealed log is the database's word, not only the application's: call_log refuses an entry on a
-- sealed log but its verdict (call.score, which a judge writes again after the seal), and
-- call_log_head refuses opening a sealed log again. Erasure deletes, and neither trigger fires on a
-- delete. Each CREATE TRIGGER takes a SHARE ROW EXCLUSIVE lock on its table, which waits for the
-- writes in flight and holds new ones for the instant it takes; the runner gives up after 1 s.

CREATE FUNCTION call_log_refuses_a_sealed_log() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    if exists (
        select 1 from written join call_log_head head on head.log = written.log
        where head.sealed and written.type <> 'call.score'
    ) then
        raise exception 'call_log: a sealed log takes no entry but its call.score'
            using errcode = 'restrict_violation';
    end if;
    return null;
end;
$$;

-- Once per statement, over every row it wrote, never once per row.
CREATE TRIGGER call_log_refuses_a_sealed_log AFTER INSERT ON call_log
    REFERENCING NEW TABLE AS written
    FOR EACH STATEMENT EXECUTE FUNCTION call_log_refuses_a_sealed_log();

CREATE FUNCTION call_log_head_stays_sealed() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    raise exception 'call_log_head: log % is sealed and is never opened again', old.log
        using errcode = 'restrict_violation';
end;
$$;

-- The WHEN is read without calling the function: an update that leaves the seal alone costs nothing.
CREATE TRIGGER call_log_head_stays_sealed BEFORE UPDATE ON call_log_head
    FOR EACH ROW WHEN (old.sealed AND NOT new.sealed)
    EXECUTE FUNCTION call_log_head_stays_sealed();

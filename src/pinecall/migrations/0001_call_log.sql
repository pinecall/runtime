-- 0001: the call log, and the head row that numbers it.
--
-- Two tables, one idea. call_log holds the entries a reader replays; call_log_head holds the
-- highest seq every log has handed out and whether that log has ended. A seq is born in the
-- UPDATE of the head row, so two appends racing take the same row lock in turn and no two
-- entries ever carry the same number. Nothing in call_log is ever updated or deleted, and the
-- triggers at the bottom of this file say so out loud instead of trusting everyone to know.

create table if not exists call_log_head (
    -- A log's identity: the call id, or '@' followed by the agent's slug for the agent's own log.
    log         text primary key,
    -- Who wrote first. Null only while a log was sealed before anybody wrote a line to it.
    agent       text,
    -- The call this log belongs to; null is the agent's own log, exactly as the envelope says it.
    call        text,
    -- The highest seq handed out, ephemerals counted. 0 means nobody has written yet.
    seq         bigint  not null default 0,
    -- Sealed: the call ended. Every later append is refused, and the refusal is this flag.
    sealed      boolean not null default false,
    -- When the log opened, by the clock of the process that appended. list_calls reads it.
    started_at  double precision
);

-- Every call an agent handled, oldest first, without reading a single entry.
create index if not exists call_log_head_by_agent
    on call_log_head (agent, started_at) where call is not null;

create table if not exists call_log (
    -- The envelope, field for field. call is null on the agent's own log.
    call        text,
    seq         bigint  not null,
    ts          double precision not null,
    agent       text    not null,
    type        text    not null,
    ephemeral   boolean not null,
    data        jsonb   not null,
    -- The same identity as the head row, computed by the database so the two cannot disagree,
    -- and the only column a reader ever filters on: null never sits in a primary key.
    log         text    not null generated always as (coalesce(call, '@' || agent)) stored,
    constraint call_log_one_row_per_seq primary key (log, seq),
    -- '@' opens an agent log's name. A call id starting with one could impersonate an agent.
    constraint call_log_call_never_opens_with_at check (call is null or left(call, 1) <> '@')
);

-- A log is append-only or it is not a log: a reader that replayed seq 41 yesterday must read the
-- same entry today. UPDATE and DELETE are refused for the whole statement, so a WHERE that
-- matched nothing is refused too — the refusal is about the intent, not about the rows.
create or replace function call_log_refuses_the_statement() returns trigger
    language plpgsql as $$
begin
    raise exception 'call_log is append-only: % is refused', tg_op
        using errcode = 'restrict_violation';
end;
$$;

drop trigger if exists call_log_refuses_update on call_log;
create trigger call_log_refuses_update before update on call_log
    for each statement execute function call_log_refuses_the_statement();

drop trigger if exists call_log_refuses_delete on call_log;
create trigger call_log_refuses_delete before delete on call_log
    for each statement execute function call_log_refuses_the_statement();

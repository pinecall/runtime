-- Erasure: the one path that may delete from call_log, and the trail of what it deleted.
-- The trigger still refuses every UPDATE, and every DELETE a door of the runtime could send; only
-- a transaction that set pinecall.erasing (tenancy/erasure.py, and nothing else) gets through.

CREATE OR REPLACE FUNCTION call_log_refuses_the_statement() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    if tg_op = 'DELETE' and current_setting('pinecall.erasing', true) = 'on' then
        return null;
    end if;
    raise exception 'call_log is append-only: % is refused', tg_op
        using errcode = 'restrict_violation';
end;
$$;

-- No foreign key to orgs: the row that says an org was erased outlives the org.
CREATE TABLE erasures (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org text NOT NULL,
    env text,
    what text NOT NULL CHECK (what IN ('call', 'contact', 'org')),
    subject text NOT NULL,
    asked_by text NOT NULL,
    calls integer NOT NULL,
    entries bigint NOT NULL,
    memories integer NOT NULL,
    recordings integer NOT NULL,
    at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX erasures_by_org ON erasures (org, at DESC);

-- A phone call's detail record, kept when the call is erased: the numbers, the times and how it
-- ended — no name, no words, no outcome. A carrier's traceback asks who placed a call, when and
-- from which number, and an erasure must not take the answer. The nightly retention run forgets a
-- record 24 months after its call started. No foreign key: a record outlives its org, as the
-- erasure trail does.

CREATE TABLE call_records (
    call text PRIMARY KEY,
    org text,
    env text CHECK (env IN ('production', 'sandbox')),
    direction text,
    from_number text,
    to_number text,
    started_at double precision,
    ended_at double precision,
    end_reason text,
    erased_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX call_records_from ON call_records (from_number, started_at);
CREATE INDEX call_records_to ON call_records (to_number, started_at);

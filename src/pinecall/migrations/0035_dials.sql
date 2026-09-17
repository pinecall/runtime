-- 0035: every outbound call this box was asked to place, taken or refused.
--
-- Two things need this row and neither is served by the call log.
--
-- The rate guard counts it. A dial that was REFUSED opens no call and writes no log, and a burst
-- of refusals is precisely the shape of somebody working out what a stolen key can reach — so the
-- ledger that the per-minute and per-day fences count from has to hold the refusals too, or the
-- fence measures only the attacks that already got through.
--
-- And it says WHO asked. A call's log says the org and the agent; it does not say which member,
-- or which machine key, pressed the button. When a bill arrives for four hundred calls to one
-- range overnight, "whose key" is the first question and this is the only place that answers it.
--
-- It is a projection, like call_facts (0025): every row restates something a door already knew,
-- the log stays the truth for the calls that opened, and losing this table would cost the counts
-- and the audit and nothing else.

CREATE TABLE IF NOT EXISTS dials (
    id        bigserial PRIMARY KEY,
    org       text NOT NULL,
    env       text NOT NULL CHECK (env IN ('production', 'sandbox')),
    agent     text NOT NULL,
    -- The call this became, or NULL when a guard refused it before any call existed.
    call      text,
    -- Where it was aimed, E.164, and which of the org's numbers it would have shown.
    dialled   text NOT NULL,
    shown     text,
    -- The member id whose key it was, or the key id for a machine key. Never a key.
    asked_by  text NOT NULL,
    -- The guard that said no, in one word, or NULL for a dial that was placed.
    refused   text,
    at        timestamptz NOT NULL DEFAULT now()
);

-- The two windows the guard asks about are both "this org, since a moment", and it asks on every
-- dial. The table is created two statements up and holds no row a write could be waiting on.
-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS dials_by_org ON dials (org, at DESC);

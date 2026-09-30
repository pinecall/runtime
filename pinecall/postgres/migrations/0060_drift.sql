-- Drift: each day's stage latencies and judges' verdicts, by agent and by the config version a
-- call ran on, folded once when the call is sealed (log/drift.py), so /v1/insights and
-- /v1/insights/drift read a few rows instead of a day of the log.
--
-- stage_days keeps a stage's seconds as a fixed histogram (log/_histogram.py: 94 buckets, each 10 %
-- wider than the last): two calls' histograms add bucket by bucket, and a median or p95 read off
-- one is within 5 %. judge_days counts each judge's settled verdicts under the hash of the
-- question it asked. config_version 0 is a call that ran on no version of its scope's settings;
-- vendor and model '' are a turn whose report named none, as the PRIMARY KEY cannot hold a null.
-- drift_calls is one row per call folded: it keeps the fold from counting a call twice, and holds
-- the verdicts it counted, so a call judged again replaces them rather than adding.
--
-- Three new tables: CREATE TABLE locks nothing that exists but `orgs`, in SHARE ROW EXCLUSIVE for
-- each foreign key, for the instant of the statement: reads of orgs go on, a write to it waits.

CREATE TABLE stage_days (
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    env text NOT NULL CHECK (env IN ('production', 'sandbox')),
    holder text NOT NULL,
    agent text NOT NULL,
    day date NOT NULL,
    config_version integer NOT NULL,
    stage text NOT NULL CHECK (stage IN ('stt', 'llm', 'tts')),
    vendor text NOT NULL,
    model text NOT NULL,
    turns integer NOT NULL,
    buckets integer[] NOT NULL,
    confidence_sum double precision NOT NULL,
    confidence_turns integer NOT NULL,
    PRIMARY KEY (org, env, holder, day, agent, config_version, stage, vendor, model)
);

CREATE TABLE judge_days (
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    env text NOT NULL CHECK (env IN ('production', 'sandbox')),
    holder text NOT NULL,
    agent text NOT NULL,
    day date NOT NULL,
    config_version integer NOT NULL,
    judge text NOT NULL,
    criteria text NOT NULL,
    held integer NOT NULL,
    broken integer NOT NULL,
    PRIMARY KEY (org, env, holder, day, agent, config_version, judge, criteria)
);

-- A version's rows across its days, for a drift between two versions.
CREATE INDEX stage_days_by_version ON stage_days (org, env, holder, agent, config_version);
CREATE INDEX judge_days_by_version ON judge_days (org, env, holder, agent, config_version);

CREATE TABLE drift_calls (
    call text PRIMARY KEY,
    org text NOT NULL REFERENCES orgs(id) ON DELETE CASCADE,
    env text NOT NULL,
    holder text NOT NULL,
    agent text NOT NULL,
    day date NOT NULL,
    config_version integer NOT NULL,
    verdicts jsonb NOT NULL
);

CREATE INDEX drift_calls_by_day ON drift_calls (org, day);

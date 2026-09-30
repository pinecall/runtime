-- What each org used in a world, a row per calendar month (UTC) of its calls' summaries: admission
-- reads the org's rows instead of every summary it ever wrote. The database keeps them, whichever
-- release writes: a summary adds its usage in the transaction that writes it, an erasure that
-- deletes it takes it away (while its head still names the org), and a log whose org or world
-- changes carries its summaries' usage over. The fold mirrors `_used_by_a_call` in
-- pinecall/log/reduce.py; `pinecall-runtime usage rebuild` refolds the table from the log in
-- Python, and the suite holds the two to each other.
--
-- Locks and time: the CREATE TRIGGERs take SHARE ROW EXCLUSIVE on call_log and call_log_head,
-- held to this transaction's commit, so every append waits while it runs and none is written
-- between the backfill's read and the trigger that counts the next one (the runner gives up
-- waiting for the locks after 1 s). The backfill reads every summary once through
-- call_log_metered (type, position) and folds it: with N summaries, a few microseconds each
-- (production today holds a few thousand: well under a second of appends held). An append that
-- waits past its statement timeout is answered 503, and a worker retries it.

CREATE TABLE usage_totals (
    org text NOT NULL,
    env text NOT NULL,
    period date NOT NULL,
    calls bigint NOT NULL DEFAULT 0,
    minutes double precision NOT NULL DEFAULT 0,
    messages bigint NOT NULL DEFAULT 0,
    input_tokens bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    characters bigint NOT NULL DEFAULT 0,
    cost_usd double precision NOT NULL DEFAULT 0,
    PRIMARY KEY (org, env, period)
);

-- A number the summary holds, or 0 where it holds none: a wrong shape never refuses an append.
CREATE FUNCTION usage_number(value jsonb) RETURNS numeric
    LANGUAGE sql IMMUTABLE
    AS $$ SELECT CASE WHEN jsonb_typeof(value) = 'number' THEN value::numeric ELSE 0 END $$;

CREATE FUNCTION usage_rows(summary jsonb, kind text) RETURNS SETOF jsonb
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT row FROM jsonb_array_elements(
        CASE WHEN jsonb_typeof(summary -> 'usage') = 'array' THEN summary -> 'usage'
             ELSE '[]'::jsonb END) AS row
    WHERE row ->> 'type' = kind
$$;

-- One summary's usage, as `_used_by_a_call` counts it.
CREATE FUNCTION summary_usage(
    summary jsonb,
    OUT minutes double precision, OUT messages bigint, OUT input_tokens bigint,
    OUT output_tokens bigint, OUT characters bigint, OUT cost_usd double precision
)
    LANGUAGE sql IMMUTABLE
    AS $$
    SELECT usage_number(summary -> 'duration_s')::float8 / 60,
           usage_number(summary -> 'turns')::bigint,
           coalesce((SELECT sum(usage_number(row -> 'input_tokens'))
                     FROM usage_rows(summary, 'llm_usage') row), 0)::bigint,
           coalesce((SELECT sum(usage_number(row -> 'output_tokens'))
                     FROM usage_rows(summary, 'llm_usage') row), 0)::bigint,
           coalesce((SELECT sum(usage_number(row -> 'characters_count'))
                     FROM usage_rows(summary, 'tts_usage') row), 0)::bigint,
           usage_number(summary -> 'cost' -> 'usd')::float8
$$;

CREATE FUNCTION usage_period(ts double precision) RETURNS date
    LANGUAGE sql IMMUTABLE
    AS $$ SELECT date_trunc('month', to_timestamp(ts) AT TIME ZONE 'UTC')::date $$;

-- Added to, or taken from with sign -1, by the summaries of logs as their heads name them now.
CREATE FUNCTION usage_counted(summaries jsonb, sign integer) RETURNS void
    LANGUAGE sql
    AS $$
    INSERT INTO usage_totals AS total
        (org, env, period, calls, minutes, messages, input_tokens, output_tokens, characters,
         cost_usd)
    SELECT one ->> 'org', one ->> 'env', usage_period((one ->> 'ts')::float8), sign * count(*),
           sign * sum(used.minutes), sign * sum(used.messages), sign * sum(used.input_tokens),
           sign * sum(used.output_tokens), sign * sum(used.characters), sign * sum(used.cost_usd)
    FROM jsonb_array_elements(summaries) AS one
    CROSS JOIN LATERAL summary_usage(one -> 'data') AS used
    WHERE one ->> 'org' IS NOT NULL AND one ->> 'env' IS NOT NULL
    GROUP BY 1, 2, 3
    ORDER BY 1, 2, 3
    ON CONFLICT (org, env, period) DO UPDATE SET
        calls = total.calls + excluded.calls,
        minutes = total.minutes + excluded.minutes,
        messages = total.messages + excluded.messages,
        input_tokens = total.input_tokens + excluded.input_tokens,
        output_tokens = total.output_tokens + excluded.output_tokens,
        characters = total.characters + excluded.characters,
        cost_usd = total.cost_usd + excluded.cost_usd
$$;

-- Most statements write no summary: they leave after one look at their rows.
CREATE FUNCTION usage_of_summaries_written() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    if not exists (select 1 from summaries where type = 'call.summary') then
        return null;
    end if;
    perform usage_counted(coalesce(jsonb_agg(jsonb_build_object(
        'org', head.org, 'env', head.env, 'ts', entry.ts, 'data', entry.data)), '[]'), 1)
    from summaries entry join call_log_head head on head.log = entry.log
    where entry.type = 'call.summary';
    return null;
end;
$$;

CREATE FUNCTION usage_of_summaries_erased() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    perform usage_counted(coalesce(jsonb_agg(jsonb_build_object(
        'org', head.org, 'env', head.env, 'ts', entry.ts, 'data', entry.data)), '[]'), -1)
    from summaries entry join call_log_head head on head.log = entry.log
    where entry.type = 'call.summary';
    return null;
end;
$$;

CREATE FUNCTION usage_of_a_log_moved() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
    perform usage_counted(coalesce(jsonb_agg(jsonb_build_object(
        'org', old.org, 'env', old.env, 'ts', entry.ts, 'data', entry.data)), '[]'), -1),
            usage_counted(coalesce(jsonb_agg(jsonb_build_object(
        'org', new.org, 'env', new.env, 'ts', entry.ts, 'data', entry.data)), '[]'), 1)
    from call_log entry
    where entry.log = new.log and entry.type = 'call.summary';
    return null;
end;
$$;

CREATE TRIGGER usage_of_summaries_written AFTER INSERT ON call_log
    REFERENCING NEW TABLE AS summaries
    FOR EACH STATEMENT EXECUTE FUNCTION usage_of_summaries_written();

-- Erasure deletes a log's entries in a statement of their own, before its head.
CREATE TRIGGER usage_of_summaries_erased AFTER DELETE ON call_log
    REFERENCING OLD TABLE AS summaries
    FOR EACH STATEMENT EXECUTE FUNCTION usage_of_summaries_erased();

CREATE TRIGGER usage_of_a_log_moved AFTER UPDATE OF org, env ON call_log_head
    FOR EACH ROW WHEN (old.org IS DISTINCT FROM new.org OR old.env IS DISTINCT FROM new.env)
    EXECUTE FUNCTION usage_of_a_log_moved();

-- The backfill: every summary of a log its head names an org and a world for.
INSERT INTO usage_totals
    (org, env, period, calls, minutes, messages, input_tokens, output_tokens, characters, cost_usd)
SELECT head.org, head.env, usage_period(entry.ts), count(*), sum(used.minutes),
       sum(used.messages), sum(used.input_tokens), sum(used.output_tokens), sum(used.characters),
       sum(used.cost_usd)
FROM call_log entry
JOIN call_log_head head ON head.log = entry.log
CROSS JOIN LATERAL summary_usage(entry.data) AS used
WHERE entry.type = 'call.summary' AND head.org IS NOT NULL AND head.env IS NOT NULL
GROUP BY 1, 2, 3;

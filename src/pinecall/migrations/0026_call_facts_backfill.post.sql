-- 0026, post-deployment: the calls from before 0025 folded into call_facts, and the corner's index.
--
-- Run by a person, `pinecall-runtime migrate up --post`, never at startup: it reads every row of
-- call_log that a fact is made of, which on a box with months of calls is minutes, not seconds.
-- Until it runs, those calls are listed and counted in no day, exactly as 0025 says.
--
-- It restates log/facts.py in SQL, once, for rows that were written before the store folded them
-- as it appended. It fills only calls that have NO row: a call that 0025's store already indexed
-- is the store's, and this never touches it. The two cannot disagree about a call.
--
-- And the index every list, day and inbox narrows by: the org's calls in one world and corner,
-- newest first. The head table holds a row per call ever taken, so it is built here and not at
-- startup. Not CONCURRENTLY: the runner applies a file inside one transaction, which CONCURRENTLY
-- cannot run in, and a post-deployment file is the one a person runs at a quiet moment of their
-- choosing — appends to call_log_head wait for the build, reads do not.

-- squawk-ignore require-concurrent-index-creation
CREATE INDEX IF NOT EXISTS call_log_head_by_corner
    ON call_log_head (org, env, holder, started_at DESC) WHERE call IS NOT NULL;

INSERT INTO call_facts (
    call, channel, direction, from_number, to_number, name, contact, spoken, ended_at, end_reason,
    outcome, cost_eur, judged, held, passed, reason, escalated, promised, e2e, heard_at,
    last_text, last_at, last_in
)
SELECT
    head.log,
    line.data ->> 'channel',
    coalesce(line.data ->> 'direction',
             CASE WHEN line.type = 'call.dialing' THEN 'outbound' ELSE 'inbound' END),
    line.data ->> 'from',
    line.data ->> 'to',
    line.data -> 'caller' ->> 'name',
    coalesce(line.data -> 'caller' ->> 'id', line.data ->> 'from'),
    coalesce(line.data ->> 'channel' = 'phone', false)
        OR EXISTS (SELECT 1 FROM call_log room WHERE room.log = head.log AND room.type = 'room.opened'),
    (ended.data ->> 'ended_at')::double precision,
    coalesce(ended.data ->> 'reason', summary.data ->> 'reason'),
    summary.data ->> 'outcome',
    (summary.data -> 'cost' ->> 'eur')::double precision,
    score.judged,
    score.held,
    CASE WHEN jsonb_typeof(score.data -> 'passed') = 'boolean' THEN (score.data ->> 'passed')::boolean
         WHEN score.judged > 0 THEN score.held = score.judged END,
    score.reason,
    coalesce(ended.data ->> 'reason' IN ('transferred', 'supervisor_ended'), false)
        OR EXISTS (
            SELECT 1 FROM call_log person
            WHERE person.log = head.log
              AND person.type IN ('supervisor.took_over', 'supervisor.said', 'supervisor.ended',
                                  'supervisor.transferred', 'call.transferred')
        ),
    coalesce(score.promised, false),
    coalesce((
        SELECT array_agg((turn.data -> 'metrics' ->> 'e2e_latency')::double precision ORDER BY turn.seq)
        FROM call_log turn
        WHERE turn.log = head.log AND turn.type = 'turn.agent'
          AND jsonb_typeof(turn.data -> 'metrics' -> 'e2e_latency') = 'number'
    ), '{}'),
    coalesce((
        SELECT array_agg(turn.ts ORDER BY turn.seq)
        FROM call_log turn WHERE turn.log = head.log AND turn.type = 'turn.user'
    ), '{}'),
    last.data ->> 'text',
    last.ts,
    last.type = 'turn.user'
FROM call_log_head head
LEFT JOIN LATERAL (
    SELECT type, data FROM call_log
    WHERE log = head.log AND type IN ('call.started', 'call.ringing', 'call.dialing')
    ORDER BY (type = 'call.started') DESC, seq DESC LIMIT 1
) line ON true
LEFT JOIN LATERAL (
    SELECT data FROM call_log WHERE log = head.log AND type = 'call.ended' ORDER BY seq DESC LIMIT 1
) ended ON true
LEFT JOIN LATERAL (
    SELECT data FROM call_log WHERE log = head.log AND type = 'call.summary' ORDER BY seq DESC LIMIT 1
) summary ON true
LEFT JOIN LATERAL (
    SELECT entry.data,
           (SELECT count(*) FROM jsonb_array_elements(entry.data -> 'judges') one
             WHERE one ->> 'verdict' IN ('held', 'broken'))::integer AS judged,
           (SELECT count(*) FROM jsonb_array_elements(entry.data -> 'judges') one
             WHERE one ->> 'verdict' = 'held')::integer AS held,
           (SELECT one ->> 'reason' FROM jsonb_array_elements(entry.data -> 'judges') one
             WHERE one ->> 'verdict' = 'broken' LIMIT 1) AS reason,
           EXISTS (SELECT 1 FROM jsonb_array_elements(entry.data -> 'judges') one
             WHERE one ->> 'verdict' = 'broken' AND one ->> 'name' = 'promises') AS promised
    FROM call_log entry WHERE entry.log = head.log AND entry.type = 'call.score'
    ORDER BY entry.seq DESC LIMIT 1
) score ON true
LEFT JOIN LATERAL (
    SELECT type, ts, data FROM call_log
    WHERE log = head.log AND type IN ('turn.user', 'turn.agent')
    ORDER BY seq DESC LIMIT 1
) last ON true
WHERE head.call IS NOT NULL
ON CONFLICT (call) DO NOTHING;

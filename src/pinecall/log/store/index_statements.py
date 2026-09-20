"""Every SQL statement the call index runs: the fold's upsert, and the questions across calls."""

from __future__ import annotations

# One entry's change laid over the row, in one statement: the columns an entry names win, the two
# arrays grow, the two flags only ever turn on, and a verdict — `$13`, scored — replaces the last
# one whole. log/facts.py's `CallFacts.changed` is the same rule, for the memory store.
FACTS_CHANGED = """
insert into call_facts as f (
    call, channel, direction, from_number, to_number, name, contact, spoken, ended_at, end_reason,
    outcome, cost_eur, judged, held, passed, reason, promised, escalated, e2e, heard_at,
    last_text, last_at, last_in, persona
) values (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $14, $15, $16, $17, $18, $19,
    $20::double precision[], $21::double precision[], $22, $23, $24, $25
)
on conflict (call) do update set
    channel     = coalesce(excluded.channel, f.channel),
    direction   = coalesce(excluded.direction, f.direction),
    from_number = coalesce(excluded.from_number, f.from_number),
    to_number   = coalesce(excluded.to_number, f.to_number),
    name        = coalesce(excluded.name, f.name),
    contact     = coalesce(excluded.contact, f.contact),
    persona     = coalesce(excluded.persona, f.persona),
    spoken      = f.spoken or excluded.spoken,
    ended_at    = coalesce(excluded.ended_at, f.ended_at),
    end_reason  = coalesce(f.end_reason, excluded.end_reason),
    outcome     = coalesce(excluded.outcome, f.outcome),
    cost_eur    = coalesce(excluded.cost_eur, f.cost_eur),
    judged      = case when $13::boolean then excluded.judged else f.judged end,
    held        = case when $13::boolean then excluded.held else f.held end,
    passed      = case when $13::boolean then excluded.passed else f.passed end,
    reason      = case when $13::boolean then excluded.reason else f.reason end,
    promised    = case when $13::boolean then excluded.promised else f.promised end,
    escalated   = f.escalated or excluded.escalated,
    e2e         = f.e2e || excluded.e2e,
    heard_at    = f.heard_at || excluded.heard_at,
    last_text   = case when excluded.last_at is null then f.last_text else excluded.last_text end,
    last_at     = coalesce(excluded.last_at, f.last_at),
    last_in     = case when excluded.last_at is null then f.last_in else excluded.last_in end
"""

# A row from before 0024 has no corner and is production's, the org's own, as the list reads it.
CORNER_OF_CALL = """
select org, coalesce(env, 'production') as env, coalesce(holder, '') as holder, agent,
       config_version, lexicon_version
from call_log_head where log = $1 and call is not null
"""

# The row as a CallFacts is built from, with the head row's agent beside it.
FACTS_OF = """
select f.*, head.agent
from call_facts f join call_log_head head on head.log = f.call
where f.call = any($1::text[])
"""

# Every spoken call whose head row never sealed and whose newest entry is older than `$1`. The
# scan is over the head rows that are still open, which is the live floor plus whatever a dead
# worker left behind — tens of rows on a busy box — and `max(entry.ts)` reads one call's entries
# through the log's own (call, seq) index. Oldest first, so a backlog is worked from the far end.
UNSEALED_SPOKEN = """
select head.log as call, head.agent,
       coalesce(head.started_at, 0) as started_at,
       coalesce(max(entry.ts), head.started_at, 0) as last_at
from call_log_head head
join call_facts f on f.call = head.log
left join call_log entry on entry.call = head.log
where head.call is not null and not head.sealed and f.spoken
group by head.log, head.agent, head.started_at
having coalesce(max(entry.ts), head.started_at, 0) < $1
order by last_at
limit $2
"""

# The corner's calls that match, the same WHERE twice: once counted, once paged. A NULL parameter
# is "any"; `$6` is the words as a LIKE pattern already escaped, `$7` their digits. Rows with no
# facts yet are listed when nothing but the agent is asked, as the plain list always listed them.
_MATCHING = """
from call_log_head head left join call_facts f on f.call = head.log
where head.org = $1 and head.call is not null and head.env = $2 and head.holder = $3
  and ($4::text is null or head.agent = $4)
  and ($5::text is null or f.channel = $5)
  and ($6::text is null
       or lower(head.log) like lower($6) || '%'
       or ($7::text <> '' and (regexp_replace(coalesce(f.from_number, ''), '\\D', '', 'g')
                                   like '%' || $7 || '%'
                              or regexp_replace(coalesce(f.to_number, ''), '\\D', '', 'g')
                                   like '%' || $7 || '%'))
       or f.name ilike '%' || $6 || '%'
       or f.outcome ilike '%' || $6 || '%')
"""

FOUND_COUNT = "select count(*) as total " + _MATCHING

FOUND_PAGE = (
    "select head.log as call "
    + _MATCHING
    + """
  and ($8::text is null or (coalesce(head.started_at, -1), head.log) < (
        select coalesce(before.started_at, -1), before.log
        from call_log_head before where before.log = $8))
order by coalesce(head.started_at, -1) desc, head.log desc
limit $9
"""
)

# One pass over the corner's calls: the day, the day before, what finished and whether a person
# was in it, what it cost, the three doors, every call ever, and the ones still open. `$4` opens
# the day and `$5` closes it.
DAY = """
select
    count(*) filter (where head.started_at >= $4 and head.started_at < $5) as calls,
    count(*) filter (where head.started_at >= $4 - ($5 - $4) and head.started_at < $4) as yesterday,
    count(*) filter (where head.started_at >= $4 and head.started_at < $5
                       and f.ended_at is not null) as finished,
    count(*) filter (where head.started_at >= $4 and head.started_at < $5
                       and f.ended_at is not null and not f.escalated) as unescalated,
    coalesce(sum(f.cost_eur) filter (where head.started_at >= $4 and head.started_at < $5), 0)
        as spent,
    count(*) filter (where head.started_at >= $4 and head.started_at < $5
                       and f.channel = 'phone') as phone,
    count(*) filter (where head.started_at >= $4 and head.started_at < $5
                       and f.channel = 'web') as web,
    count(*) filter (where head.started_at >= $4 and head.started_at < $5
                       and f.channel = 'whatsapp') as whatsapp,
    count(*) as total,
    count(*) filter (where not head.sealed) as live
from call_log_head head left join call_facts f on f.call = head.log
where head.org = $1 and head.env = $2 and head.holder = $3 and head.call is not null
"""

DAY_MEDIAN_E2E = """
select percentile_cont(0.5) within group (order by turn.seconds) as median
from call_log_head head
join call_facts f on f.call = head.log
cross join lateral unnest(f.e2e) as turn(seconds)
where head.org = $1 and head.env = $2 and head.holder = $3 and head.call is not null
  and head.started_at >= $4 and head.started_at < $5
"""

DAY_BY_AGENT = """
select head.agent as slug, count(*) as calls,
       avg(f.held::double precision / f.judged) filter (where f.judged > 0) as score
from call_log_head head left join call_facts f on f.call = head.log
where head.org = $1 and head.env = $2 and head.holder = $3 and head.call is not null
  and head.started_at >= $4 and head.started_at < $5
group by head.agent
order by calls desc, slug
"""

# A budget is the org's, so every world and corner is summed.
SPENT_BETWEEN = """
select coalesce(sum(f.cost_eur), 0) as spent
from call_log_head head join call_facts f on f.call = head.log
where head.org = $1 and head.call is not null and head.started_at >= $2 and head.started_at < $3
"""

# The inbox: an agent's calls in one corner grouped by contact. `newest` is each contact's call that
# moved last; `counted` is how many calls and what the reader `$5` has not read — a spoken call is
# one thing to read, a written one each message the contact sent. `$6`/`$7` is the cursor.
THREADS = """
with mine as (
    select f.*, head.agent, coalesce(head.started_at, -1) as at,
           coalesce(f.last_at, head.started_at, -1) as moved_at
    from call_log_head head join call_facts f on f.call = head.log
    where head.org = $1 and head.env = $2 and head.holder = $3 and head.agent = $4
      and head.call is not null and f.contact is not null
), newest as (
    select distinct on (contact) *
    from mine order by contact, moved_at desc, call desc
), counted as (
    select mine.contact, count(*) as calls, max(mine.name) as any_name,
           sum(case when mine.spoken then (mine.at > coalesce(seen.read_at, 0))::int
                    else (select count(*) from unnest(mine.heard_at) as heard
                          where heard > coalesce(seen.read_at, 0))::int end) as unread
    from mine
    left join thread_reads seen
      on seen.org = $1 and seen.env = $2 and seen.holder = $3 and seen.agent = $4
     and seen.reader = $5 and seen.contact = mine.contact
    group by mine.contact
)
select newest.*, counted.calls, counted.unread, coalesce(newest.name, counted.any_name) as known_as
from newest join counted on counted.contact = newest.contact
where $6::double precision is null or (newest.moved_at, newest.contact) < ($6, $7::text)
order by newest.moved_at desc, newest.contact desc
limit $8
"""

# The runs of one synthetic caller, in one corner, newest first: the same WHERE twice, once
# counted and once paged, exactly as the session list asks its own question. call_facts_by_persona
# (0046) finds the caller's calls and the join narrows them to the corner. `$5` is the cursor.
_THE_CALLERS_RUNS = """
from call_log_head head join call_facts f on f.call = head.log
where head.org = $1 and head.env = $2 and head.holder = $3 and head.call is not null
  and f.persona = $4
"""

PERSONA_RUNS_COUNT = "select count(*) as total " + _THE_CALLERS_RUNS

PERSONA_RUNS_PAGE = (
    """
select f.*, head.agent, coalesce(head.started_at, -1) as started_at,
       coalesce(array_length(f.heard_at, 1), 0) as turns
"""
    + _THE_CALLERS_RUNS
    + """
  and ($5::text is null or (coalesce(head.started_at, -1), head.log) < (
        select coalesce(before.started_at, -1), before.log
        from call_log_head before where before.log = $5))
order by coalesce(head.started_at, -1) desc, head.log desc
limit $6
"""
)

CALLS_WITH = """
select head.log as call
from call_log_head head join call_facts f on f.call = head.log
where head.org = $1 and head.env = $2 and head.holder = $3 and head.agent = $4
  and head.call is not null and f.contact = $5
order by coalesce(head.started_at, -1) desc, head.log desc
limit $6
"""

# The dial guard's one question, and it is a point lookup: call_facts_by_contact (0025) finds the
# contact's calls and the join narrows them to the org. No corner and no agent — a number that
# reached this org at all is a number this org may call back.
EVER_REACHED = """
select exists (
    select 1 from call_log_head head join call_facts f on f.call = head.log
    where head.org = $1 and head.call is not null and f.contact = $2
) as reached
"""

# A cursor only moves forward: two tabs marking one thread read never move it back.
READ = """
insert into thread_reads as seen (org, env, holder, agent, reader, contact, read_at)
values ($1, $2, $3, $4, $5, $6, $7)
on conflict (org, env, holder, agent, reader, contact)
do update set read_at = greatest(seen.read_at, excluded.read_at)
"""

# The one append a sealed log takes: a verdict reached after the call was over. The seq is born in
# the same row lock APPEND takes, and the sealed flag is not asked.
RESCORED = """
with numbered as (
    update call_log_head set seq = seq + 1 where log = $1 returning seq
), written as (
    insert into call_log (call, seq, ts, agent, type, ephemeral, data)
    select $1, numbered.seq, $3::double precision, $2, 'call.score', false, $4::jsonb
    from numbered
)
select seq from numbered
"""

"""Every SQL statement the postgres store runs, named once, beside the head-row shape they share."""

from __future__ import annotations

# ── the statements ──────────────────────────────────────────────────────────────

# One statement, one seq. The head row is inserted or bumped under its own row lock, so two
# appends racing queue up instead of reading one number twice; the entry is written in the same
# statement from the number that came out. A sealed log matches no WHERE, returns no row, and
# that empty result is the refusal — the store never asks "is it sealed?" separately and then acts.
APPEND = """
with numbered as (
    insert into call_log_head as head (log, agent, call, seq, started_at)
    values ($1, $2, $3, 1, $4::double precision)
    on conflict (log) do update
        set seq        = head.seq + 1,
            agent      = coalesce(head.agent, excluded.agent),
            call       = coalesce(head.call, excluded.call),
            started_at = coalesce(head.started_at, excluded.started_at)
        where not head.sealed
    returning seq
), written as (
    insert into call_log (call, seq, ts, agent, type, ephemeral, data)
    select $3, numbered.seq, $4::double precision, $2, $5, $6::boolean, $7::jsonb
    from numbered
    where not $6::boolean
)
select seq from numbered
"""

# The one thing a reader ever asks for: what is above my cursor, in order, at most this many.
PAGE = """
select call, seq, ts, agent, type, ephemeral, data
from call_log
where log = $1 and seq > $2
order by seq
limit $3
"""

# Sealing a log nobody wrote to is legal: a recovery path can reach the end before the beginning.
SEAL = """
insert into call_log_head (log, call, sealed) values ($1, $1, true)
on conflict (log) do update set sealed = true
"""

# The head row knows the call's whole life, so a call whose every entry was ephemeral is still
# listed. started_at is the appending process's clock, which is the only clock the entries have.
LIST_CALLS = """
select call from call_log_head
where agent = $1 and call is not null
order by started_at nulls last, log
"""

LATEST_SEQ = "select seq from call_log_head where log = $1"

# The org's calls across every agent, newest first: what the console's Sessions screen lists at
# the org level. The head row carries the org (0006) and the clock the entries have (started_at).
# A NULL parameter means "any": the org's every call, or one world's, or one corner's, or one
# agent's. The corner columns are 0024's; rows from before it read as production, the org's own.
CALLS_OF = """
select call from call_log_head
where org = $1 and call is not null
  and ($3::text is null or env = $3)
  and ($4::text is null or holder = $4)
  and ($5::text is null or agent = $5)
order by started_at desc nulls last, log desc
limit $2
"""

# The head row is where a log's owner lives, and the first claim stands: a log is opened under one
# key and never moves. The row may not exist yet — a claim can land before the first entry — so
# it is inserted with a seq of 0, which is what APPEND's own insert would have written.
OWNED = """
insert into call_log_head as head
    (log, agent, call, org, env, holder, config_version, lexicon_version)
values ($1, $2, $3, $4, $5, $6, $7, $8)
on conflict (log) do update
    set org             = coalesce(head.org, excluded.org),
        env             = coalesce(head.env, excluded.env),
        holder          = coalesce(head.holder, excluded.holder),
        config_version  = coalesce(head.config_version, excluded.config_version),
        lexicon_version = coalesce(head.lexicon_version, excluded.lexicon_version)
"""

# Every head row this agent has: its own, and one per call it took. `call_log_head_by_agent`
# indexes exactly this column, so the move is one statement and one index scan however long the
# agent has been running.
MOVED = "update call_log_head set org = $2 where agent = $1"

# What `UPDATE n` says when it moved nothing. The tag is the only thing that tells an agent
# nobody has ever registered from one that moved: a verb that answered yes to a typo would send
# an operator looking for the change in the wrong org.
MOVED_NOTHING = "UPDATE 0"

OWNER = "select org from call_log_head where log = $1"

# The one read that spans every log: the metered types, by position, each with its log's owner.
# The partial index in 0006 is exactly this WHERE and ORDER BY.
ACROSS = """
select entry.position, head.org, entry.call, entry.seq, entry.ts, entry.agent, entry.type,
       entry.ephemeral, entry.data
from call_log entry
join call_log_head head on head.log = entry.log
where entry.type = any($1::text[]) and entry.position > $2
order by entry.position
limit $3
"""

# The two questions that span every log instead of asking about one. The Store protocol answers
# about ONE log, which is what keeps it portable, so these live on the Postgres store alone: only
# an operator asks them, and the CLI that does already holds this pool.
CALLS_NEWEST_FIRST = """
select call from call_log_head
where call is not null and ($2::text is null or agent = $2)
order by started_at desc nulls last, log desc
limit $1
"""

# Live means the log was never sealed: the call ended when somebody wrote its last entry.
NEWEST_LIVE_CALL = """
select call from call_log_head
where call is not null and not sealed
order by started_at desc nulls last, log desc
limit 1
"""

# The one health fact the doctor prints about a database, asked from the module that holds the
# driver so no CLI has to import one.
INSTALLED_EXTENSIONS = "select extname from pg_extension"

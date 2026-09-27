"""What is known of every call without folding its log: the facts row, and the lists over it."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from typing import LiteralString

from psycopg import sql
from psycopg.rows import DictRow

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.types import Corner, Env, Versions
from pinecall.postgres.pool import Connection, Pool
from pinecall.wire.events import (
    AgentTurnEnded,
    CallDialing,
    CallEnded,
    CallRinging,
    CallScore,
    CallStarted,
    CallSummary,
    RoomOpened,
    UserTurnEnded,
    event_of,
)
from pinecall.wire.frames import Entry, WireModel

# The judge whose broken verdict raises the promise flag (evals/judges.py).
PROMISES = "promises"

# skipped and deferred are not judged.
SETTLED = frozenset({"held", "broken"})

ENDED_BY_A_PERSON = frozenset({"transferred", "supervisor_ended"})

# A whisper is not one of these: the agent keeps the conversation.
A_PERSON_TOOK_PART = frozenset(
    {
        "supervisor.took_over",
        "supervisor.said",
        "supervisor.ended",
        "supervisor.transferred",
        "call.transferred",
    }
)

# A web call is spoken only once a room opens.
SPOKEN_CHANNEL = "phone"

# Days are cut in UTC: an org has no timezone.
A_DAY_S = 24 * 60 * 60

# The columns of call_facts, in the order the row is written; the dataclass names them the same.
COLUMNS = (
    "call",
    "channel",
    "direction",
    "from_number",
    "to_number",
    "name",
    "contact",
    "persona",
    "spoken",
    "ended_at",
    "end_reason",
    "outcome",
    "cost_eur",
    "judged",
    "held",
    "passed",
    "reason",
    "escalated",
    "promised",
    "e2e",
    "heard_at",
    "last_text",
    "last_at",
    "last_in",
)
ARRAYS = frozenset({"e2e", "heard_at"})

FACTS_LOCKED = "select * from call_facts where call = %(call)s for update"

# The whole row is written every time: the merge happened in fold(), in one place.
FACTS_WRITTEN = sql.SQL(
    "insert into call_facts ({columns}) values ({values})"
    " on conflict (call) do update set {updates}"
).format(
    columns=sql.SQL(", ").join(sql.Identifier(column) for column in COLUMNS),
    values=sql.SQL(", ").join(sql.Placeholder(column) for column in COLUMNS),
    updates=sql.SQL(", ").join(
        sql.SQL("{0} = excluded.{0}").format(sql.Identifier(column)) for column in COLUMNS[1:]
    ),
)

# Head rows older than the env and holder columns read as production, the org's own.
CORNER_OF_CALL = """
select org, coalesce(env, 'production') as env, coalesce(holder, '') as holder, agent,
       config_version, lexicon_version, sealed, started_at
from call_log_head where log = %(call)s and call is not null
"""

FACTS_OF = """
select f.*, head.agent
from call_facts f join call_log_head head on head.log = f.call
where f.call = any(%(calls)s)
"""

# call_log is joined on log, its primary key's first column, never on call, which scans it.
# A call that never reached call.started may have no facts row, hence the left join.
UNSEALED_SPOKEN = """
select head.log as call, head.agent,
       coalesce(head.started_at, 0) as started_at,
       coalesce(max(entry.ts), head.started_at, 0) as last_at, null as channel
from call_log_head head
left join call_facts f on f.call = head.log
left join call_log entry on entry.log = head.log
where head.call is not null and not head.sealed
  and (coalesce(f.spoken, false)
       or not exists (select 1 from call_log began
                       where began.log = head.log and began.type = 'call.started'))
group by head.log, head.agent, head.started_at
having coalesce(max(entry.ts), head.started_at, 0) < %(quiet_since)s
order by last_at
limit %(limit)s
"""

UNSEALED_WRITTEN = """
select head.log as call, head.agent,
       coalesce(head.started_at, 0) as started_at,
       coalesce(max(entry.ts), head.started_at, 0) as last_at, f.channel
from call_log_head head
join call_facts f on f.call = head.log
left join call_log entry on entry.log = head.log
where head.call is not null and not head.sealed and not coalesce(f.spoken, false)
  and exists (select 1 from call_log began
               where began.log = head.log and began.type = 'call.started')
group by head.log, head.agent, head.started_at, f.channel
having coalesce(max(entry.ts), head.started_at, 0) < %(quiet_since)s
order by last_at
limit %(limit)s
"""

# words is an escaped LIKE pattern and digits its digits; a call with no facts row still matches
# a filter on the agent alone.
_MATCHING = sql.SQL("""
from call_log_head head left join call_facts f on f.call = head.log
where head.org = %(org)s and head.call is not null
  and head.env = %(env)s and head.holder = %(holder)s
  and (%(agent)s::text is null or head.agent = %(agent)s)
  and (%(channel)s::text is null or f.channel = %(channel)s)
  and (%(words)s::text is null
       or lower(head.log) like lower(%(words)s) || '%%'
       or (%(digits)s::text <> '' and (regexp_replace(coalesce(f.from_number, ''), '\\D', '', 'g')
                                   like '%%' || %(digits)s || '%%'
                              or regexp_replace(coalesce(f.to_number, ''), '\\D', '', 'g')
                                   like '%%' || %(digits)s || '%%'))
       or f.name ilike '%%' || %(words)s || '%%'
       or f.outcome ilike '%%' || %(words)s || '%%')
""")

# The page before this one is the cursor: a call id, whose start time and id bound the page.
_A_PAGE_BEFORE = sql.SQL("""
  and (%(before)s::text is null or (coalesce(head.started_at, -1), head.log) < (
        select coalesce(before.started_at, -1), before.log
        from call_log_head before where before.log = %(before)s))
order by coalesce(head.started_at, -1) desc, head.log desc
limit %(limit)s
""")

FOUND_COUNT = sql.SQL("select count(*) as total ") + _MATCHING
FOUND_PAGE = sql.SQL("select head.log as call ") + _MATCHING + _A_PAGE_BEFORE

_ITS_DAY = sql.SQL("head.started_at >= %(start)s and head.started_at < %(end)s")

DAY = sql.SQL("""
select
    count(*) filter (where {day}) as calls,
    count(*) filter (where head.started_at >= %(start)s - (%(end)s - %(start)s)
                       and head.started_at < %(start)s) as yesterday,
    count(*) filter (where {day} and f.ended_at is not null) as finished,
    count(*) filter (where {day} and f.ended_at is not null and not f.escalated)
        as unescalated,
    coalesce(sum(f.cost_eur) filter (where {day}), 0) as spent,
    count(*) filter (where {day} and f.channel = 'phone') as phone,
    count(*) filter (where {day} and f.channel = 'web') as web,
    count(*) filter (where {day} and f.channel = 'whatsapp') as whatsapp,
    count(*) as total,
    count(*) filter (where not head.sealed) as live
from call_log_head head left join call_facts f on f.call = head.log
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.call is not null
""").format(day=_ITS_DAY)

DAY_MEDIAN_E2E = sql.SQL("""
select percentile_cont(0.5) within group (order by turn.seconds) as median
from call_log_head head
join call_facts f on f.call = head.log
cross join lateral unnest(f.e2e) as turn(seconds)
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.call is not null and {day}
""").format(day=_ITS_DAY)

DAY_BY_AGENT = sql.SQL("""
select head.agent as slug, count(*) as calls,
       avg(f.held::double precision / f.judged) filter (where f.judged > 0) as score
from call_log_head head left join call_facts f on f.call = head.log
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.call is not null and {day}
group by head.agent
order by calls desc, slug
""").format(day=_ITS_DAY)

# A budget is the org's: every env and holder is summed.
SPENT_BETWEEN = sql.SQL("""
select coalesce(sum(f.cost_eur), 0) as spent
from call_log_head head join call_facts f on f.call = head.log
where head.org = %(org)s and head.call is not null and {day}
""").format(day=_ITS_DAY)

# Unread for the reader: one per spoken call started since they read, one per line the contact
# wrote since then in a written call.
THREADS = """
with mine as (
    select f.*, head.agent, coalesce(head.started_at, -1) as at,
           coalesce(f.last_at, head.started_at, -1) as moved_at
    from call_log_head head join call_facts f on f.call = head.log
    where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
      and head.agent = %(agent)s and head.call is not null and f.contact is not null
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
      on seen.org = %(org)s and seen.env = %(env)s and seen.holder = %(holder)s
     and seen.agent = %(agent)s and seen.reader = %(reader)s and seen.contact = mine.contact
    group by mine.contact
)
select newest.*, counted.calls, counted.unread, coalesce(newest.name, counted.any_name) as known_as
from newest join counted on counted.contact = newest.contact
where %(moved_at)s::double precision is null
   or (newest.moved_at, newest.contact) < (%(moved_at)s, %(contact)s::text)
order by newest.moved_at desc, newest.contact desc
limit %(limit)s
"""

_THE_PERSONAS_RUNS = sql.SQL("""
from call_log_head head join call_facts f on f.call = head.log
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.call is not null and f.persona = %(persona)s
""")

PERSONA_RUNS_COUNT = sql.SQL("select count(*) as total ") + _THE_PERSONAS_RUNS
PERSONA_RUNS_PAGE = (
    sql.SQL("select f.*, head.agent, coalesce(head.started_at, -1) as started_at ")
    + _THE_PERSONAS_RUNS
    + _A_PAGE_BEFORE
)

CALLS_WITH = """
select head.log as call
from call_log_head head join call_facts f on f.call = head.log
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.agent = %(agent)s and head.call is not null and f.contact = %(contact)s
order by coalesce(head.started_at, -1) desc, head.log desc
limit %(limit)s
"""

# The org's, on purpose: any past contact allows a call back. The contact is matched as stored.
EVER_REACHED = """
select exists (
    select 1 from call_log_head head join call_facts f on f.call = head.log
    where head.org = %(org)s and head.call is not null and f.contact = %(contact)s
) as reached
"""

# A read cursor never moves back.
READ = """
insert into thread_reads as seen (org, env, holder, agent, reader, contact, read_at)
values (%(org)s, %(env)s, %(holder)s, %(agent)s, %(reader)s, %(contact)s, %(at)s)
on conflict (org, env, holder, agent, reader, contact)
do update set read_at = greatest(seen.read_at, excluded.read_at)
"""


# Every field comes from some entry, so the row can always be folded again from the log.
@dataclass(frozen=True, slots=True)
class CallFacts:
    """One call as its lists, counts and inbox see it."""

    call: str
    agent: str = ""
    channel: str | None = None
    direction: str | None = None
    from_number: str | None = None
    to_number: str | None = None
    name: str | None = None
    # The inbox key: the app's contact id, else the number or the visitor id.
    contact: str | None = None
    persona: str | None = None
    spoken: bool = False
    ended_at: float | None = None
    end_reason: str | None = None
    outcome: str | None = None
    cost_eur: float | None = None
    judged: int | None = None
    held: int | None = None
    passed: bool | None = None
    reason: str | None = None
    escalated: bool = False
    promised: bool = False
    e2e: tuple[float, ...] = ()
    heard_at: tuple[float, ...] = ()
    last_text: str | None = None
    last_at: float | None = None
    last_in: bool | None = None

    @property
    def score(self) -> dict[str, object] | None:
        """Return the score as the wire carries it, or None while no judge settled."""
        if not self.judged:
            return None
        return {
            "held": self.held or 0,
            "judged": self.judged,
            "passed": self.passed is not False,
            "reason": self.reason,
        }

    @property
    def flags(self) -> list[str]:
        """Return the review flags raised, in the wire's order."""
        raised = (
            ("escalated", self.escalated),
            ("low_score", self.passed is False),
            ("promise", self.promised),
        )
        return [flag for flag, up in raised if up]


@dataclass(frozen=True, slots=True)
class CallCorner:
    """Where a call was opened: its corner, its agent, and what its head row says."""

    corner: Corner | None
    agent: str
    versions: Versions
    sealed: bool
    started_at: float | None


@dataclass(frozen=True, slots=True)
class Wanted:
    """What a call list asks for: an agent, a channel, words, and the page before this one."""

    agent: str | None = None
    # Matched case-insensitively against the call id's start, either number's digits, the
    # caller's name and the outcome.
    q: str | None = None
    channel: str | None = None
    before: str | None = None


@dataclass(frozen=True, slots=True)
class Found:
    """One page of calls, newest first, the total and the cursor of the next page."""

    calls: list[str]
    total: int
    next: str | None


@dataclass(frozen=True, slots=True)
class Unsealed:
    """A call still open, for the reaper: when it started, when it last moved, its channel."""

    call: str
    agent: str
    started_at: float
    last_at: float
    channel: str | None


@dataclass(frozen=True, slots=True)
class AgentDay:
    """One agent's calls in a day and the share of judgments it held."""

    slug: str
    calls: int
    score: float | None


@dataclass(frozen=True, slots=True)
class Day:
    """A corner's day in numbers."""

    calls: int
    yesterday: int
    finished: int
    unescalated: int
    median_e2e: float | None
    spent: float
    channels: dict[str, int]
    agents: list[AgentDay]
    total: int
    live: int


@dataclass(frozen=True, slots=True)
class Thread:
    """One contact in an inbox: its newest call, when it moved, what the reader has not read."""

    contact: str
    newest: CallFacts
    moved_at: float
    unread: int
    calls: int
    name: str | None


@dataclass(frozen=True, slots=True)
class Threads:
    """One page of an inbox and the cursor of the next."""

    rows: list[Thread]
    next: str | None


@dataclass(frozen=True, slots=True)
class Inbox:
    """Whose inbox: a corner, an agent and the person reading it."""

    corner: Corner
    agent: str
    reader: str


@dataclass(frozen=True, slots=True)
class PersonaRun:
    """One simulated call by a persona: when it started, its facts, how many turns it took."""

    started_at: float
    facts: CallFacts
    turns: int = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "turns", len(self.facts.heard_at))


@dataclass(frozen=True, slots=True)
class PersonaRuns:
    """One page of a persona's runs, newest first, the total and the cursor of the next page."""

    runs: list[PersonaRun]
    total: int
    next: str | None


# ── the fold ──


def fold(facts: CallFacts, entry: Entry) -> CallFacts:
    """Return the facts with what the entry says; an entry that says nothing to them, unchanged."""
    if entry.call is None or entry.ephemeral:
        return facts
    folded = facts
    match _readable(entry):
        case CallRinging() | CallDialing() | CallStarted() as line:
            folded = _on_the_line(facts, line)
        case CallEnded() | CallSummary() as over:
            folded = _over(facts, over)
        case CallScore() as score:
            folded = _scored(facts, score)
        case RoomOpened():
            folded = replace(facts, spoken=True)
        case UserTurnEnded() | AgentTurnEnded() as turn:
            folded = _said(facts, entry, turn)
        case _ if entry.type in A_PERSON_TOOK_PART:
            folded = replace(facts, escalated=True)
        case _:
            pass
    return folded


def _readable(entry: Entry) -> WireModel | None:
    try:
        return event_of(entry)
    except DeclarationRefused:
        return None


def _on_the_line(facts: CallFacts, line: CallRinging | CallDialing | CallStarted) -> CallFacts:
    caller = line.caller
    direction = line.direction if isinstance(line, CallStarted) else None
    persona = line.persona if isinstance(line, CallStarted) else None
    return replace(
        facts,
        channel=line.channel,
        direction=direction or ("outbound" if isinstance(line, CallDialing) else "inbound"),
        from_number=line.from_,
        to_number=line.to,
        name=facts.name if caller is None else caller.name or facts.name,
        contact=(caller.id if caller is not None and caller.id else None) or line.from_,
        persona=persona or facts.persona,
        spoken=facts.spoken or line.channel == SPOKEN_CHANNEL,
    )


# The reason the call ended with stands; the summary's is taken only when there was none.
def _over(facts: CallFacts, over: CallEnded | CallSummary) -> CallFacts:
    if isinstance(over, CallEnded):
        return replace(
            facts,
            ended_at=over.ended_at,
            end_reason=facts.end_reason or over.reason,
            escalated=facts.escalated or over.reason in ENDED_BY_A_PERSON,
        )
    return replace(
        facts,
        outcome=over.outcome,
        cost_eur=over.cost.eur,
        end_reason=facts.end_reason or over.reason,
    )


# A verdict replaces the one before it whole.
def _scored(facts: CallFacts, score: CallScore) -> CallFacts:
    settled = [one for one in score.judges if one.verdict in SETTLED]
    broken = [one for one in settled if one.verdict == "broken"]
    passed = score.passed if score.passed is not None else (not broken if settled else None)
    return replace(
        facts,
        judged=len(settled),
        held=len(settled) - len(broken),
        passed=passed,
        reason=broken[0].reason if broken else None,
        promised=any(one.name == PROMISES for one in broken),
    )


def _said(facts: CallFacts, entry: Entry, turn: UserTurnEnded | AgentTurnEnded) -> CallFacts:
    if isinstance(turn, UserTurnEnded):
        return replace(
            facts,
            heard_at=(*facts.heard_at, entry.ts),
            last_text=turn.text,
            last_at=entry.ts,
            last_in=True,
        )
    e2e = turn.metrics.e2e_latency
    return replace(
        facts,
        e2e=facts.e2e if e2e is None else (*facts.e2e, e2e),
        last_text=turn.text,
        last_at=entry.ts,
        last_in=False,
    )


# On the append's connection, so folds land in seq order under the head row's lock.
async def record(connection: Connection, entry: Entry) -> None:
    """Fold the entry into its call's facts row, in the transaction the entry is written in."""
    if entry.call is None or entry.ephemeral:
        return
    row = await (await connection.execute(FACTS_LOCKED, {"call": entry.call})).fetchone()
    facts = CallFacts(call=entry.call) if row is None else facts_of(row)
    folded = fold(facts, entry)
    if folded == facts:
        return
    written = {**asdict(folded), "e2e": list(folded.e2e), "heard_at": list(folded.heard_at)}
    written.pop("agent")
    await connection.execute(FACTS_WRITTEN, written)


def facts_of(row: DictRow) -> CallFacts:
    """Return the facts a call_facts row holds, with its agent when the head row was joined."""
    facts = CallFacts(**{name: row[name] for name in COLUMNS if name not in ARRAYS})
    return replace(
        facts, agent=row.get("agent") or "", e2e=tuple(row["e2e"]), heard_at=tuple(row["heard_at"])
    )


# ── the lists ──


async def corner_of_call(pool: Pool, call: str) -> CallCorner | None:
    """Return where the call was opened, or None for a call nobody wrote to or claimed."""
    async with pool.connection() as connection:
        row = await (await connection.execute(CORNER_OF_CALL, {"call": call})).fetchone()
    if row is None:
        return None
    env: Env = "sandbox" if row["env"] == "sandbox" else "production"
    return CallCorner(
        corner=None if row["org"] is None else Corner(row["org"], env, row["holder"]),
        agent=row["agent"] or "",
        versions=Versions(config=row["config_version"], lexicon=row["lexicon_version"]),
        sealed=row["sealed"],
        started_at=row["started_at"],
    )


async def facts_of_calls(pool: Pool, calls: Sequence[str]) -> dict[str, CallFacts]:
    """Return the facts of each of the calls that has a row."""
    async with pool.connection() as connection:
        rows = await (await connection.execute(FACTS_OF, {"calls": list(calls)})).fetchall()
    return {str(row["call"]): facts_of(row) for row in rows}


# Across every org: the reaper's question. A call that never reached call.started is reaped
# whatever its channel.
async def unsealed_spoken(pool: Pool, quiet_since: float, *, limit: int) -> list[Unsealed]:
    """Return the spoken or never-started calls still open and quiet since then, oldest first."""
    return await _unsealed(pool, UNSEALED_SPOKEN, quiet_since, limit)


# A written call idles out where it runs; this finds the ones a restart left open.
async def unsealed_written(pool: Pool, quiet_since: float, *, limit: int) -> list[Unsealed]:
    """Return the written calls still open and quiet since then, with their channel."""
    return await _unsealed(pool, UNSEALED_WRITTEN, quiet_since, limit)


async def found(pool: Pool, corner: Corner, wanted: Wanted, *, limit: int) -> Found:
    """Return one page of the corner's calls the filter matches, newest first, and their total."""
    asked = {
        **asdict(corner),
        "agent": wanted.agent,
        "channel": wanted.channel,
        "words": None if wanted.q is None else _like_escaped(wanted.q),
        "digits": "".join(one for one in (wanted.q or "") if one.isdigit()),
    }
    async with pool.connection() as connection:
        total = await (await connection.execute(FOUND_COUNT, asked)).fetchone()
        page = {**asked, "before": wanted.before, "limit": limit + 1}
        rows = await (await connection.execute(FOUND_PAGE, page)).fetchall()
    calls = [str(row["call"]) for row in rows]
    return Found(
        calls=calls[:limit],
        total=0 if total is None else int(total["total"]),
        next=calls[limit - 1] if len(calls) > limit else None,
    )


async def runs_of_persona(
    pool: Pool, corner: Corner, persona: str, *, before: str | None, limit: int
) -> PersonaRuns:
    """Return one page of the persona's runs in the corner, newest first, and their total."""
    asked = {**asdict(corner), "persona": persona}
    async with pool.connection() as connection:
        total = await (await connection.execute(PERSONA_RUNS_COUNT, asked)).fetchone()
        page = {**asked, "before": before, "limit": limit + 1}
        rows = await (await connection.execute(PERSONA_RUNS_PAGE, page)).fetchall()
    runs = [PersonaRun(started_at=float(row["started_at"]), facts=facts_of(row)) for row in rows]
    return PersonaRuns(
        runs=runs[:limit],
        total=0 if total is None else int(total["total"]),
        next=runs[limit - 1].facts.call if len(runs) > limit else None,
    )


async def counted_day(pool: Pool, corner: Corner, start: float) -> Day:
    """Return the corner's day that begins at start, in numbers."""
    asked = {**asdict(corner), "start": start, "end": start + A_DAY_S}
    async with pool.connection() as connection:
        counted = await (await connection.execute(DAY, asked)).fetchone()
        median = await (await connection.execute(DAY_MEDIAN_E2E, asked)).fetchone()
        agents = await (await connection.execute(DAY_BY_AGENT, asked)).fetchall()
    if counted is None:
        raise DeclarationRefused("a day counts, even an empty one")
    return Day(
        calls=int(counted["calls"]),
        yesterday=int(counted["yesterday"]),
        finished=int(counted["finished"]),
        unescalated=int(counted["unescalated"]),
        median_e2e=None if median is None else median["median"],
        spent=float(counted["spent"]),
        channels={door: int(counted[door]) for door in ("phone", "web", "whatsapp")},
        agents=[
            AgentDay(slug=row["slug"], calls=row["calls"], score=row["score"]) for row in agents
        ],
        total=int(counted["total"]),
        live=int(counted["live"]),
    )


async def spent_between(pool: Pool, org: str, start: float, end: float) -> float:
    """Return what the org's calls started in [start, end) cost, every env and holder counted."""
    asked = {"org": org, "start": start, "end": end}
    async with pool.connection() as connection:
        row = await (await connection.execute(SPENT_BETWEEN, asked)).fetchone()
    return 0.0 if row is None else float(row["spent"])


async def threads(pool: Pool, inbox: Inbox, *, after: str | None, limit: int) -> Threads:
    """Return one page of the inbox, the contact that moved last first, with the unread counts."""
    moved_at, contact = _after_the_cursor(after)
    asked = {
        **asdict(inbox.corner),
        "agent": inbox.agent,
        "reader": inbox.reader,
        "moved_at": moved_at,
        "contact": contact,
        "limit": limit + 1,
    }
    async with pool.connection() as connection:
        rows = await (await connection.execute(THREADS, asked)).fetchall()
    found = [
        Thread(
            contact=str(row["contact"]),
            newest=facts_of(row),
            moved_at=float(row["moved_at"]),
            unread=int(row["unread"] or 0),
            calls=int(row["calls"]),
            name=row["known_as"],
        )
        for row in rows
    ]
    last = found[limit - 1] if len(found) > limit else None
    return Threads(
        rows=found[:limit], next=None if last is None else f"{last.moved_at!r}:{last.contact}"
    )


async def calls_with(
    pool: Pool, corner: Corner, agent: str, contact: str, *, limit: int
) -> list[str]:
    """Return the contact's newest calls with the agent in the corner."""
    asked = {**asdict(corner), "agent": agent, "contact": contact, "limit": limit}
    async with pool.connection() as connection:
        rows = await (await connection.execute(CALLS_WITH, asked)).fetchall()
    return [str(row["call"]) for row in rows]


async def ever_reached(pool: Pool, org: str, contact: str) -> bool:
    """Return whether the contact ever had a call with any agent of the org, in any env."""
    async with pool.connection() as connection:
        row = await (
            await connection.execute(EVER_REACHED, {"org": org, "contact": contact})
        ).fetchone()
    return row is not None and bool(row["reached"])


async def read(pool: Pool, inbox: Inbox, contact: str, at: float) -> None:
    """Mark the contact's thread read up to that time for this reader."""
    asked = {
        **asdict(inbox.corner),
        "agent": inbox.agent,
        "reader": inbox.reader,
        "contact": contact,
        "at": at,
    }
    async with pool.connection() as connection:
        await connection.execute(READ, asked)


async def _unsealed(
    pool: Pool, query: LiteralString, quiet_since: float, limit: int
) -> list[Unsealed]:
    async with pool.connection() as connection:
        cursor = await connection.execute(query, {"quiet_since": quiet_since, "limit": limit})
        rows = await cursor.fetchall()
    return [
        Unsealed(
            call=str(row["call"]),
            agent=str(row["agent"] or ""),
            started_at=float(row["started_at"]),
            last_at=float(row["last_at"]),
            channel=row["channel"],
        )
        for row in rows
    ]


def _after_the_cursor(cursor: str | None) -> tuple[float | None, str | None]:
    if cursor is None or ":" not in cursor:
        return None, None
    moved_at, contact = cursor.split(":", 1)
    try:
        return float(moved_at), contact
    except ValueError:
        return None, None


def _like_escaped(words: str) -> str:
    return words.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

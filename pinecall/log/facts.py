"""What is known of every call without folding its log: the facts row, written as entries land."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace

from psycopg import sql
from psycopg.rows import DictRow

from pinecall.domain.agent import Versions
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Connection, Pool
from pinecall.wire.events import (
    EVENTS,
    AgentTurnEnded,
    CallDialing,
    CallEnded,
    CallRinging,
    CallStarted,
    CallSummary,
    UserTurnEnded,
    event_of,
)
from pinecall.wire.frames import Entry, WireModel
from pinecall.wire.parts import PLATFORM, Cost
from pinecall.wire.room import RoomOpened
from pinecall.wire.scores import CallScore

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
    "cost_usd",
    "cost_llm_usd",
    "cost_stt_usd",
    "cost_tts_usd",
    "cost_phone_usd",
    "cost_platform_usd",
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

# What a summary's cost is folded by: a row's unit says its stage, and the box's own rows
# (PLATFORM) are the platform's, whatever their unit.
STAGES = ("llm", "stt", "tts", "phone", "platform")
STAGE_OF_UNIT = {
    "input_tokens": "llm",
    "cached_input_tokens": "llm",
    "cache_creation_tokens": "llm",
    "output_tokens": "llm",
    "characters": "tts",
    "audio_seconds": "stt",
    "minutes": "phone",
}


# In the order of the calls, as the heads are locked: two writers never wait in a circle.
FACTS_LOCKED = "select * from call_facts where call = any(%(calls)s) order by call for update"


LENT = """
INSERT INTO call_facts (call, lent) VALUES (%(call)s, %(lent)s)
ON CONFLICT (call) DO UPDATE SET lent = excluded.lent
"""


# Head rows older than the env and holder columns read as production, the org's own.
CORNER_OF_CALL = """
select org, coalesce(env, 'production') as env, coalesce(holder, '') as holder, agent,
       config_version, lexicon_version, sealed, started_at, written
from call_log_head where log = %(call)s and call is not null
"""


FACTS_OF = """
select f.*, head.agent
from call_facts f join call_log_head head on head.log = f.call
where f.call = any(%(calls)s)
"""


# call_log is joined on log, its primary key's first column, never on call, which scans it.
# A call that never reached call.started may have no facts row, hence the left join.
# A head claimed at the open, whose first entry waits on the writer, moved last when it opened; a
# head with no entry, no start and no opening (a seal's lease) is never quiet. Under load this is
# the whole window: at 300 calls held the reaper sealed calls 36 ms after they opened (2026-10-01).
UNSEALED_SPOKEN = """
select head.log as call, head.agent,
       coalesce(head.started_at, 0) as started_at,
       coalesce(max(entry.ts), head.started_at, extract(epoch from opening.opened_at)) as last_at,
       null as channel, head.env
from call_log_head head
left join call_facts f on f.call = head.log
left join call_openings opening on opening.call = head.log
left join call_log entry on entry.log = head.log
where head.call is not null and not head.sealed
  and (coalesce(f.spoken, false)
       or not exists (select 1 from call_log began
                       where began.log = head.log and began.type = 'call.started'))
group by head.log, head.agent, head.started_at, head.env, opening.opened_at
having coalesce(max(entry.ts), head.started_at, extract(epoch from opening.opened_at))
       < %(quiet_since)s
order by last_at
limit %(limit)s
"""


UNSEALED_WRITTEN = """
select head.log as call, head.agent,
       coalesce(head.started_at, 0) as started_at,
       coalesce(max(entry.ts), head.started_at, 0) as last_at, f.channel, head.env
from call_log_head head
join call_facts f on f.call = head.log
left join call_log entry on entry.log = head.log
where head.call is not null and not head.sealed and not coalesce(f.spoken, false)
  and exists (select 1 from call_log began
               where began.log = head.log and began.type = 'call.started')
group by head.log, head.agent, head.started_at, head.env, f.channel
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


_IN_THE_WINDOW = sql.SQL("head.started_at >= %(start)s and head.started_at < %(end)s")


# A window's rows: the scope's calls, or one agent's when the window names one.
_THE_WINDOWS_CALLS = sql.SQL("""
from call_log_head head left join call_facts f on f.call = head.log
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.call is not null and (%(agent)s::text is null or head.agent = %(agent)s)
""")


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
  and head.agent = %(agent)s and head.call is not null and f.persona = %(persona)s
""")


CALLS_WITH = """
select head.log as call
from call_log_head head join call_facts f on f.call = head.log
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.agent = %(agent)s and head.call is not null and f.contact = %(contact)s
order by coalesce(head.started_at, -1) desc, head.log desc
limit %(limit)s
"""


# Any agent of the org, on purpose: a past contact allows a call back. In one world: a test call
# from a phone in the sandbox never makes it dialable from production. Matched as stored.
EVER_REACHED = """
select exists (
    select 1 from call_log_head head join call_facts f on f.call = head.log
    where head.org = %(org)s and head.env = %(env)s and head.call is not null
      and f.contact = %(contact)s
) as reached
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
    cost_usd: float | None = None
    cost_llm_usd: float | None = None
    cost_stt_usd: float | None = None
    cost_tts_usd: float | None = None
    cost_phone_usd: float | None = None
    cost_platform_usd: float | None = None
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
class CallScope:
    """Where a call was opened: its scope, its agent, and what its head row says."""

    scope: Scope | None
    agent: str
    versions: Versions
    sealed: bool
    started_at: float | None
    # The entries its worker's writer sent: where a writer that takes the call over follows on.
    written: int


# The types fold() reads; every other entry leaves a call's facts as they are, so a group of only
# those never locks or reads a facts row (most of a call's entries: its metrics, its transcripts).
FOLDED_TYPES = (
    frozenset(
        name
        for name, model in EVENTS.items()
        if model
        in (
            CallRinging,
            CallDialing,
            CallStarted,
            CallEnded,
            CallSummary,
            CallScore,
            RoomOpened,
            UserTurnEnded,
            AgentTurnEnded,
        )
    )
    | A_PERSON_TOOK_PART
)


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


FOUND_COUNT = sql.SQL("select count(*) as total ") + _MATCHING


FOUND_PAGE = sql.SQL("select head.log as call ") + _MATCHING + _A_PAGE_BEFORE


# "before" is the window of the same length right before it. A call is judged once a judge
# settled, and passed unless a judge said it did not (CallFacts.score).
WINDOW = (
    sql.SQL("""
select
    count(*) filter (where {window}) as calls,
    count(*) filter (where head.started_at >= %(start)s - (%(end)s - %(start)s)
                       and head.started_at < %(start)s) as before,
    count(*) filter (where {window} and f.ended_at is not null) as finished,
    count(*) filter (where {window} and f.ended_at is not null and not f.escalated)
        as unescalated,
    count(*) filter (where {window} and f.judged > 0) as judged,
    count(*) filter (where {window} and f.judged > 0 and f.passed is not false) as passed,
    count(*) filter (where {window} and f.escalated) as escalated,
    avg(f.ended_at - head.started_at) filter (where {window} and f.ended_at is not null)
        as mean_length,
    coalesce(sum(f.cost_usd) filter (where {window}), 0) as spent,
    count(*) filter (where {window} and f.channel = 'phone') as phone,
    count(*) filter (where {window} and f.channel = 'web') as web,
    count(*) filter (where {window} and f.channel = 'whatsapp') as whatsapp,
    count(*) as total,
    count(*) filter (where not head.sealed) as live
""").format(window=_IN_THE_WINDOW)
    + _THE_WINDOWS_CALLS
)


WINDOW_MEDIAN_E2E = sql.SQL("""
select percentile_cont(0.5) within group (order by turn.seconds) as median
from call_log_head head
join call_facts f on f.call = head.log
cross join lateral unnest(f.e2e) as turn(seconds)
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.call is not null and (%(agent)s::text is null or head.agent = %(agent)s)
  and {window}
""").format(window=_IN_THE_WINDOW)


WINDOW_BY_AGENT = (
    sql.SQL("""
select head.agent as slug, count(*) as calls,
       avg(f.held::double precision / f.judged) filter (where f.judged > 0) as score,
       coalesce(sum(f.cost_llm_usd), 0) as llm, coalesce(sum(f.cost_stt_usd), 0) as stt,
       coalesce(sum(f.cost_tts_usd), 0) as tts, coalesce(sum(f.cost_phone_usd), 0) as phone,
       coalesce(sum(f.cost_platform_usd), 0) as platform,
       coalesce(sum(f.ended_at - head.started_at) filter (where f.ended_at is not null), 0) / 60
           as minutes
""")
    + _THE_WINDOWS_CALLS
    + sql.SQL("""
  and {window}
group by head.agent
order by calls desc, slug
""").format(window=_IN_THE_WINDOW)
)


WINDOW_ENDINGS = (
    sql.SQL("select f.end_reason as reason, count(*) as count ")
    + _THE_WINDOWS_CALLS
    + sql.SQL("""
  and {window} and f.end_reason is not null
group by f.end_reason
order by count desc, reason
""").format(window=_IN_THE_WINDOW)
)


# Days are cut in UTC, as everywhere: the day of a call is its start's whole days since the epoch.
WINDOW_BY_DAY = (
    sql.SQL("""
select floor(head.started_at / %(a_day)s)::bigint as epoch_day,
       count(*) filter (where f.channel = 'phone') as phone,
       count(*) filter (where f.channel = 'web') as web,
       count(*) filter (where f.channel = 'whatsapp') as whatsapp,
       coalesce(sum(f.cost_usd), 0) as spent,
       count(*) filter (where f.judged > 0) as judged,
       count(*) filter (where f.judged > 0 and f.passed is not false) as passed
""")
    + _THE_WINDOWS_CALLS
    + sql.SQL("""
  and {window}
group by epoch_day
""").format(window=_IN_THE_WINDOW)
)


PERSONA_RUNS_COUNT = sql.SQL("select count(*) as total ") + _THE_PERSONAS_RUNS


PERSONA_RUNS_PAGE = (
    sql.SQL("select f.*, head.agent, coalesce(head.started_at, -1) as started_at ")
    + _THE_PERSONAS_RUNS
    + _A_PAGE_BEFORE
)


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
            folded = _turn_folded(facts, entry, turn)
        case _ if entry.type in A_PERSON_TOOK_PART:
            folded = replace(facts, escalated=True)
        case _:
            pass
    return folded


# On the writer's connection, so folds land in seq order under the head rows' locks. The entries
# are each call's in seq order: every call's row is locked at once, folded in memory, and the rows
# that moved are written in one round of statements.
async def record(connection: Connection, entries: Sequence[Entry]) -> None:
    """Fold the entries into their calls' facts rows, in the transaction they are written in."""
    by_call: dict[str, list[Entry]] = {}
    for entry in entries:
        if entry.call is not None and not entry.ephemeral and entry.type in FOLDED_TYPES:
            by_call.setdefault(entry.call, []).append(entry)
    if not by_call:
        return
    locked = await connection.execute(FACTS_LOCKED, {"calls": sorted(by_call)})
    known = {str(row["call"]): facts_of(row) for row in await locked.fetchall()}
    written: list[Mapping[str, object]] = []
    for call, kept in by_call.items():
        facts = known.get(call, CallFacts(call=call))
        folded = facts
        for entry in kept:
            folded = fold(folded, entry)
        if folded != facts:
            row = {**asdict(folded), "e2e": list(folded.e2e), "heard_at": list(folded.heard_at)}
            row.pop("agent")
            written.append(row)
    if written:
        async with connection.cursor() as cursor:
            await cursor.executemany(FACTS_WRITTEN, written)


def facts_of(row: DictRow) -> CallFacts:
    """Return the facts a call_facts row holds, with its agent when the head row was joined."""
    facts = CallFacts(**{name: row[name] for name in COLUMNS if name not in ARRAYS})
    return replace(
        facts, agent=row.get("agent") or "", e2e=tuple(row["e2e"]), heard_at=tuple(row["heard_at"])
    )


# Not a fold: the vendors a call ran on the box's key are not in its entries, the gateway knows
# them at the seal.
async def lent(pool: Pool, call: str, vendors: Sequence[str]) -> None:
    """Keep the vendors the call ran on the box's own key with its facts."""
    async with pool.connection() as connection:
        await connection.execute(LENT, {"call": call, "lent": list(vendors)})


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
        cost_usd=over.cost.usd,
        end_reason=facts.end_reason or over.reason,
        **_cost_by_stage(over.cost),
    )


def _cost_by_stage(cost: Cost) -> dict[str, float]:
    summed: dict[str, float] = dict.fromkeys(STAGES, 0.0)
    for row in cost.rows:
        stage = "platform" if row.provider == PLATFORM else STAGE_OF_UNIT[row.unit]
        summed[stage] += row.usd
    return {f"cost_{stage}_usd": round(usd, 6) for stage, usd in summed.items()}


# A verdict replaces the one before it whole.
def _scored(facts: CallFacts, score: CallScore) -> CallFacts:
    settled = [judge for judge in score.judges if judge.verdict in SETTLED]
    broken = [settled_one for settled_one in settled if settled_one.verdict == "broken"]
    passed = score.passed if score.passed is not None else (not broken if settled else None)
    return replace(
        facts,
        judged=len(settled),
        held=len(settled) - len(broken),
        passed=passed,
        reason=broken[0].reason if broken else None,
        promised=any(item.name == PROMISES for item in broken),
    )


def _turn_folded(facts: CallFacts, entry: Entry, turn: UserTurnEnded | AgentTurnEnded) -> CallFacts:
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

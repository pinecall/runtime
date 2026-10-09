"""What a scope used: a window of whole days in numbers, and the metered feed past a cursor."""

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from psycopg import sql
from psycopg.rows import DictRow

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import CHANNELS
from pinecall.domain.scope import Scope
from pinecall.log.facts import STAGES
from pinecall.log.reduce import METERED_TYPES, UsageRow, usage_row
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool

# Days are cut in UTC: an org has no timezone.
A_DAY_S = 24 * 60 * 60


IN_THE_WINDOW = sql.SQL("head.started_at >= %(start)s and head.started_at < %(end)s")


# A window's rows: the scope's calls, or one agent's when the window names one.
THE_WINDOWS_CALLS = sql.SQL("""
from call_log_head head left join call_facts f on f.call = head.log
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.call is not null and (%(agent)s::text is null or head.agent = %(agent)s)
""")


@dataclass(frozen=True, slots=True)
class AgentWindow:
    """One agent's window: its calls, the share of judgments it held, what it cost by stage."""

    slug: str
    calls: int
    score: float | None
    # Dollars by stage (llm, stt, tts, phone, platform), and the minutes of the calls that ended.
    spend: dict[str, float]
    minutes: float


@dataclass(frozen=True, slots=True)
class WindowDay:
    """One UTC day of a window: its calls by door, what they cost, and how their judges answered."""

    day: date
    channels: dict[str, int]
    spent: float
    judged: int
    passed: int


@dataclass(frozen=True, slots=True)
class Window:
    """A scope's window of whole UTC days in numbers, and the window of the same length before."""

    calls: int
    before: int
    finished: int
    unescalated: int
    judged: int
    passed: int
    escalated: int
    mean_length: float | None
    median_e2e: float | None
    spent: float
    channels: dict[str, int]
    endings: list[tuple[str, int]]
    days: list[WindowDay]
    agents: list[AgentWindow]
    total: int
    live: int


@dataclass(frozen=True, slots=True)
class MeteredPage:
    """One page of the usage feed: the rows kept, and the cursor past everything read."""

    rows: list[UsageRow]
    next: int | None


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
""").format(window=IN_THE_WINDOW)
    + THE_WINDOWS_CALLS
)


WINDOW_MEDIAN_E2E = sql.SQL("""
select percentile_cont(0.5) within group (order by turn.seconds) as median
from call_log_head head
join call_facts f on f.call = head.log
cross join lateral unnest(f.e2e) as turn(seconds)
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.call is not null and (%(agent)s::text is null or head.agent = %(agent)s)
  and {window}
""").format(window=IN_THE_WINDOW)


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
    + THE_WINDOWS_CALLS
    + sql.SQL("""
  and {window}
group by head.agent
order by calls desc, slug
""").format(window=IN_THE_WINDOW)
)


WINDOW_ENDINGS = (
    sql.SQL("select f.end_reason as reason, count(*) as count ")
    + THE_WINDOWS_CALLS
    + sql.SQL("""
  and {window} and f.end_reason is not null
group by f.end_reason
order by count desc, reason
""").format(window=IN_THE_WINDOW)
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
    + THE_WINDOWS_CALLS
    + sql.SQL("""
  and {window}
group by epoch_day
""").format(window=IN_THE_WINDOW)
)


# The cursor moves past every row read, kept or not, so a page filtered to one org still moves.
async def metered_page(store: Store, *, after: int, limit: int, org: str | None) -> MeteredPage:
    """The metered rows after the cursor, of one org or of every org, folded."""
    read = [usage_row(item) for item in await store.across(METERED_TYPES, after=after, limit=limit)]
    kept = [row for row in read if org is None or row.org == org]
    return MeteredPage(rows=kept, next=read[-1].cursor if read else None)


# One agent's window when it names one; the whole scope's when it is None.
async def counted_window(
    pool: Pool, scope: Scope, start: float, end: float, agent: str | None
) -> Window:
    """Return the scope's window from start to end, both on a UTC midnight, in numbers."""
    params = {**asdict(scope), "start": start, "end": end, "agent": agent, "a_day": A_DAY_S}
    # independent: five counts of one window, each its own snapshot
    async with pool.connection() as connection:
        counted = await (await connection.execute(WINDOW, params)).fetchone()
        median = await (await connection.execute(WINDOW_MEDIAN_E2E, params)).fetchone()
        agents = await (await connection.execute(WINDOW_BY_AGENT, params)).fetchall()
        endings = await (await connection.execute(WINDOW_ENDINGS, params)).fetchall()
        by_day = await (await connection.execute(WINDOW_BY_DAY, params)).fetchall()
    if counted is None:
        raise DeclarationRefused("a window counts, even an empty one")
    return Window(
        calls=int(counted["calls"]),
        before=int(counted["before"]),
        finished=int(counted["finished"]),
        unescalated=int(counted["unescalated"]),
        judged=int(counted["judged"]),
        passed=int(counted["passed"]),
        escalated=int(counted["escalated"]),
        mean_length=None if counted["mean_length"] is None else float(counted["mean_length"]),
        median_e2e=None if median is None else median["median"],
        spent=float(counted["spent"]),
        channels={door: int(counted[door]) for door in CHANNELS},
        endings=[(row["reason"], int(row["count"])) for row in endings],
        days=_every_day(start, end, {int(row["epoch_day"]): row for row in by_day}),
        agents=[
            AgentWindow(
                slug=row["slug"],
                calls=row["calls"],
                score=row["score"],
                spend={stage: float(row[stage]) for stage in STAGES},
                minutes=float(row["minutes"]),
            )
            for row in agents
        ],
        total=int(counted["total"]),
        live=int(counted["live"]),
    )


# Every day of the window has its row, a day nobody called included, so a chart has no holes.
def _every_day(start: float, end: float, counted: dict[int, DictRow]) -> list[WindowDay]:
    first, last = int(start // A_DAY_S), int(end // A_DAY_S)
    return [_day_of(epoch_day, counted.get(epoch_day)) for epoch_day in range(first, last)]


def _day_of(epoch_day: int, row: DictRow | None) -> WindowDay:
    day = datetime.fromtimestamp(epoch_day * A_DAY_S, UTC).date()
    if row is None:
        return WindowDay(
            day=day, channels=dict.fromkeys(CHANNELS, 0), spent=0.0, judged=0, passed=0
        )
    return WindowDay(
        day=day,
        channels={door: int(row[door]) for door in CHANNELS},
        spent=float(row["spent"]),
        judged=int(row["judged"]),
        passed=int(row["passed"]),
    )

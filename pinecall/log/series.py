"""A scope's window as series: each UTC day's calls, endings, cost, latencies, judges and tools."""

from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from psycopg import sql
from psycopg.rows import DictRow

from pinecall.domain.scope import Scope
from pinecall.log import _histogram
from pinecall.log.usage import A_DAY_S, IN_THE_WINDOW, THE_WINDOWS_CALLS
from pinecall.postgres.pool import Pool

DAY_CALLS = (
    sql.SQL("""
select floor(head.started_at / %(a_day)s)::bigint as epoch_day, count(*) as calls,
       count(*) filter (where f.ended_at is not null) as finished,
       count(*) filter (where f.escalated) as escalated,
       coalesce(sum(f.cost_usd), 0) as spent,
       avg(f.ended_at - head.started_at) filter (where f.ended_at > head.started_at)
           as mean_length
""")
    + THE_WINDOWS_CALLS
    + sql.SQL("""
  and {window}
group by epoch_day
""").format(window=IN_THE_WINDOW)
)

# The turns are unnested apart from the counts, or a call of ten turns would count ten times.
DAY_E2E = sql.SQL("""
select floor(head.started_at / %(a_day)s)::bigint as epoch_day,
       percentile_cont(0.5) within group (order by turn.seconds) as e2e_median,
       percentile_cont(0.95) within group (order by turn.seconds) as e2e_p95
from call_log_head head join call_facts f on f.call = head.log
cross join lateral unnest(f.e2e) as turn(seconds)
where head.org = %(org)s and head.env = %(env)s and head.holder = %(holder)s
  and head.call is not null and (%(agent)s::text is null or head.agent = %(agent)s)
  and {window}
group by epoch_day
""").format(window=IN_THE_WINDOW)

DAY_ENDINGS = (
    sql.SQL("""
select floor(head.started_at / %(a_day)s)::bigint as epoch_day, f.end_reason as reason,
       count(*) as count
""")
    + THE_WINDOWS_CALLS
    + sql.SQL("""
  and {window} and f.end_reason is not null
group by epoch_day, f.end_reason
order by epoch_day, count desc, reason
""").format(window=IN_THE_WINDOW)
)

DAY_STAGES = """
select day, stage, turns, buckets
from stage_days
where org = %(org)s and env = %(env)s and holder = %(holder)s
  and day >= %(first)s and day <= %(last)s and (%(agent)s::text is null or agent = %(agent)s)
"""

DAY_JUDGES = """
select day, judge, sum(held) as held, sum(broken) as broken
from judge_days
where org = %(org)s and env = %(env)s and holder = %(holder)s
  and day >= %(first)s and day <= %(last)s and (%(agent)s::text is null or agent = %(agent)s)
group by day, judge
order by day, judge
"""

DAY_TOOLS = """
select day, sum(tools_ran) as ran, sum(tools_failed) as failed
from drift_calls
where org = %(org)s and env = %(env)s and holder = %(holder)s
  and day >= %(first)s and day <= %(last)s and (%(agent)s::text is null or agent = %(agent)s)
group by day
"""


@dataclass(frozen=True, slots=True)
class StageDay:
    """One stage over one day's turns: how many, and how slow at the median and the tail."""

    stage: str
    turns: int
    median_s: float | None
    p95_s: float | None


@dataclass(frozen=True, slots=True)
class JudgeDay:
    """One judge over one day: the verdicts that held, and those settled."""

    name: str
    held: int
    judged: int


@dataclass(frozen=True, slots=True)
class SeriesDay:
    """One UTC day of the window, every number a chart draws."""

    day: date
    calls: int = 0
    finished: int = 0
    escalated: int = 0
    spent: float = 0.0
    mean_length: float | None = None
    e2e_median: float | None = None
    e2e_p95: float | None = None
    endings: list[tuple[str, int]] = field(default_factory=list[tuple[str, int]])
    stages: list[StageDay] = field(default_factory=list[StageDay])
    judges: list[JudgeDay] = field(default_factory=list[JudgeDay])
    tools_ran: int = 0
    tools_failed: int = 0


@dataclass(frozen=True, slots=True)
class Counted:
    """What the six reads of a window answered, each a list of rows."""

    calls: list[DictRow]
    e2e: list[DictRow]
    endings: list[DictRow]
    stages: list[DictRow]
    judges: list[DictRow]
    tools: list[DictRow]


async def series_window(
    pool: Pool, scope: Scope, first: date, last: date, agent: str | None
) -> list[SeriesDay]:
    """Every UTC day from first to last, a quiet day included, in numbers."""
    params = {
        **asdict(scope),
        "start": _opening(first),
        "end": _opening(last) + A_DAY_S,
        "agent": agent,
        "a_day": A_DAY_S,
        "first": first,
        "last": last,
    }
    # independent: six reads of one window, each its own snapshot
    async with pool.connection() as connection:
        counted = Counted(
            calls=await (await connection.execute(DAY_CALLS, params)).fetchall(),
            e2e=await (await connection.execute(DAY_E2E, params)).fetchall(),
            endings=await (await connection.execute(DAY_ENDINGS, params)).fetchall(),
            stages=await (await connection.execute(DAY_STAGES, params)).fetchall(),
            judges=await (await connection.execute(DAY_JUDGES, params)).fetchall(),
            tools=await (await connection.execute(DAY_TOOLS, params)).fetchall(),
        )
    days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    return [_day(day, counted) for day in days]


def _day(day: date, counted: Counted) -> SeriesDay:
    epoch = _epoch(day)
    calls = next((row for row in counted.calls if int(row["epoch_day"]) == epoch), None)
    e2e = next((row for row in counted.e2e if int(row["epoch_day"]) == epoch), None)
    tools = next((row for row in counted.tools if row["day"] == day), None)
    return SeriesDay(
        day=day,
        calls=0 if calls is None else int(calls["calls"]),
        finished=0 if calls is None else int(calls["finished"]),
        escalated=0 if calls is None else int(calls["escalated"]),
        spent=0.0 if calls is None else float(calls["spent"]),
        mean_length=None
        if calls is None or calls["mean_length"] is None
        else float(calls["mean_length"]),
        e2e_median=None if e2e is None else e2e["e2e_median"],
        e2e_p95=None if e2e is None else e2e["e2e_p95"],
        endings=[
            (row["reason"], int(row["count"]))
            for row in counted.endings
            if int(row["epoch_day"]) == epoch
        ],
        stages=_stages_of(day, counted.stages),
        judges=[
            JudgeDay(row["judge"], int(row["held"]), int(row["held"]) + int(row["broken"]))
            for row in counted.judges
            if row["day"] == day
        ],
        tools_ran=0 if tools is None else int(tools["ran"]),
        tools_failed=0 if tools is None else int(tools["failed"]),
    )


# A day's stage is every vendor and version of it added bucket by bucket, then read by rank.
def _stages_of(day: date, rows: list[DictRow]) -> list[StageDay]:
    merged: dict[str, tuple[int, list[int]]] = {}
    for row in rows:
        if row["day"] != day:
            continue
        turns, buckets = merged.get(row["stage"], (0, _histogram.empty()))
        added = _histogram.added(buckets, row["buckets"])
        merged[row["stage"]] = (turns + int(row["turns"]), added)
    return [
        StageDay(
            stage=stage,
            turns=turns,
            median_s=_histogram.rank(buckets, _histogram.MEDIAN),
            p95_s=_histogram.rank(buckets, _histogram.P95),
        )
        for stage, (turns, buckets) in sorted(merged.items())
    ]


def _opening(day: date) -> float:
    return (day - date(1970, 1, 1)).days * float(A_DAY_S)


def _epoch(day: date) -> int:
    return (day - date(1970, 1, 1)).days

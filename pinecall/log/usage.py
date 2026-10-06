"""What a scope used: a window of whole days in numbers, and the metered feed past a cursor."""

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from psycopg.rows import DictRow

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import CHANNELS
from pinecall.domain.scope import Scope
from pinecall.log.facts import (
    A_DAY_S,
    STAGES,
    WINDOW,
    WINDOW_BY_AGENT,
    WINDOW_BY_DAY,
    WINDOW_ENDINGS,
    WINDOW_MEDIAN_E2E,
)
from pinecall.log.reduce import METERED_TYPES, UsageRow, usage_row
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool


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

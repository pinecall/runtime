"""The org's monitors: kept per world, read at every seal, each fired at most once a day."""

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from pinecall.domain.monitor import Metric, Monitor
from pinecall.domain.scope import Scope
from pinecall.log.series import SeriesDay
from pinecall.postgres.pool import Pool

LISTED = """
select id, agent, name, metric, above, threshold, window_days, created_by, fired_on, fired_value
from monitors where org = %(org)s and env = %(env)s order by created_at, id
"""
PUT = """
insert into monitors (id, org, env, agent, name, metric, above, threshold, window_days, created_by)
values (%(id)s, %(org)s, %(env)s, %(agent)s, %(name)s, %(metric)s, %(above)s, %(threshold)s,
        %(window_days)s, %(created_by)s)
"""
DROP = "delete from monitors where org = %(org)s and env = %(env)s and id = %(id)s returning id"
FIRED = """
update monitors set fired_on = %(day)s, fired_value = %(value)s
where id = %(id)s and (fired_on is null or fired_on < %(day)s) returning id
"""


@dataclass(frozen=True)
class Fired:
    """A monitor that crossed its line today, and the value that crossed it."""

    monitor: Monitor
    value: float


async def monitors_of(pool: Pool, scope: Scope) -> list[Monitor]:
    """Every monitor of the world, oldest first."""
    async with pool.connection() as connection:
        where = {"org": scope.org, "env": scope.env}
        rows = await (await connection.execute(LISTED, where)).fetchall()
    return [
        Monitor(
            id=str(row["id"]),
            name=str(row["name"]),
            metric=row["metric"],
            above=bool(row["above"]),
            threshold=float(row["threshold"]),
            window_days=int(row["window_days"]),
            agent=row["agent"],
            created_by=str(row["created_by"]),
            fired_on=row["fired_on"],
            fired_value=None if row["fired_value"] is None else float(row["fired_value"]),
        )
        for row in rows
    ]


async def put_monitor(pool: Pool, scope: Scope, monitor: Monitor, created_by: str) -> Monitor:
    """Keep a new monitor in the world; answers it with its id."""
    kept = Monitor(
        id=f"mon_{secrets.token_hex(6)}",
        name=monitor.name.strip(),
        metric=monitor.metric,
        above=monitor.above,
        threshold=monitor.threshold,
        window_days=monitor.window_days,
        agent=monitor.agent,
        created_by=created_by,
    )
    values = {
        "id": kept.id,
        "org": scope.org,
        "env": scope.env,
        "agent": kept.agent,
        "name": kept.name,
        "metric": kept.metric,
        "above": kept.above,
        "threshold": kept.threshold,
        "window_days": kept.window_days,
        "created_by": created_by,
    }
    async with pool.connection() as connection:
        await connection.execute(PUT, values)
    return kept


async def drop_monitor(pool: Pool, scope: Scope, monitor_id: str) -> bool:
    """Forget a monitor of the world; whether it was there."""
    async with pool.connection() as connection:
        where = {"org": scope.org, "env": scope.env, "id": monitor_id}
        dropped = await connection.execute(DROP, where)
        return await dropped.fetchone() is not None


async def fired_today(pool: Pool, monitor: Monitor, day: date, value: float) -> bool:
    """Mark the monitor as fired today; False when it already had."""
    async with pool.connection() as connection:
        marked = await connection.execute(FIRED, {"id": monitor.id, "day": day, "value": value})
        return await marked.fetchone() is not None


def measured(days: list[SeriesDay], metric: Metric) -> float | None:
    """The metric over the window's days: sums, shares of sums, and latencies weighted by turns."""
    return MEASURES[metric](days)


def _calls(days: list[SeriesDay]) -> float | None:
    return float(sum(day.calls for day in days))


def _spend(days: list[SeriesDay]) -> float | None:
    return sum(day.spent for day in days)


def _escalated_rate(days: list[SeriesDay]) -> float | None:
    calls = sum(day.calls for day in days)
    return None if calls == 0 else sum(day.escalated for day in days) / calls


def _held_rate(days: list[SeriesDay]) -> float | None:
    affirmed = sum(judge.held for day in days for judge in day.judges)
    settled = sum(judge.judged for day in days for judge in day.judges)
    return None if settled == 0 else affirmed / settled


def _tool_failure_rate(days: list[SeriesDay]) -> float | None:
    ran = sum(day.tools_ran for day in days)
    return None if ran == 0 else sum(day.tools_failed for day in days) / ran


def _e2e_median(days: list[SeriesDay]) -> float | None:
    known = [(day.e2e_median, day.calls) for day in days if day.e2e_median is not None]
    return _weighted(known)


def _llm_median(days: list[SeriesDay]) -> float | None:
    known = [
        (stage.median_s, stage.turns)
        for day in days
        for stage in day.stages
        if stage.stage == "llm" and stage.median_s is not None
    ]
    return _weighted(known)


def _weighted(known: list[tuple[float, int]]) -> float | None:
    weight = sum(count for _, count in known)
    return None if weight == 0 else sum(value * count for value, count in known) / weight


MEASURES: dict[Metric, Callable[[list[SeriesDay]], float | None]] = {
    "calls": _calls,
    "spend_usd": _spend,
    "escalated_rate": _escalated_rate,
    "held_rate": _held_rate,
    "tool_failure_rate": _tool_failure_rate,
    "e2e_median_s": _e2e_median,
    "llm_median_s": _llm_median,
}

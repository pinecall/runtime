"""Tests for the org's monitors: kept, listed, dropped, fired once a day, and measured."""

from datetime import date

from pinecall.domain.monitor import Monitor
from pinecall.domain.scope import Scope
from pinecall.log.series import JudgeDay, SeriesDay, StageDay
from pinecall.postgres.pool import Pool
from pinecall.tenancy.monitors import (
    drop_monitor,
    fired_today,
    measured,
    monitors_of,
    put_monitor,
)
from tests.conftest import postgres
from tests.tenancy.conftest import an_org

SLOW = Monitor(
    "", "slow answers", "e2e_median_s", above=True, threshold=2.0, window_days=7, agent="front-desk"
)


@postgres
async def test_a_monitor_is_kept_in_its_world_listed_oldest_first_and_dropped(pool: Pool) -> None:
    org = (await an_org(pool)).id
    world, other = Scope(org, "sandbox"), Scope(org, "production")
    kept = await put_monitor(pool, world, SLOW, "m_ana")
    assert kept.id.startswith("mon_")
    assert (kept.created_by, kept.fired_on) == ("m_ana", None)
    assert [kept.name for kept in await monitors_of(pool, world)] == ["slow answers"]
    assert await monitors_of(pool, other) == []
    assert await drop_monitor(pool, world, kept.id)
    assert not await drop_monitor(pool, world, kept.id)


@postgres
async def test_a_monitor_fires_once_a_day_and_keeps_the_value_that_crossed(pool: Pool) -> None:
    world = Scope((await an_org(pool)).id, "sandbox")
    kept = await put_monitor(pool, world, SLOW, "m_ana")
    assert await fired_today(pool, kept, date(2026, 10, 9), 2.4)
    assert not await fired_today(pool, kept, date(2026, 10, 9), 2.6)
    [read] = await monitors_of(pool, world)
    assert (read.fired_on, read.fired_value) == (date(2026, 10, 9), 2.4)
    assert await fired_today(pool, kept, date(2026, 10, 10), 2.6)


def test_a_metric_over_a_window_is_a_sum_a_share_or_a_latency_weighted_by_turns() -> None:
    days = [
        SeriesDay(
            day=date(2026, 10, 8),
            calls=10,
            escalated=1,
            spent=1.5,
            e2e_median=1.0,
            stages=[StageDay("llm", 20, 0.5, 1.0)],
            judges=[JudgeDay("grounded", 8, 10)],
            tools_ran=4,
            tools_failed=1,
        ),
        SeriesDay(
            day=date(2026, 10, 9),
            calls=30,
            escalated=3,
            spent=4.5,
            e2e_median=3.0,
            stages=[StageDay("llm", 60, 1.5, 3.0)],
            judges=[JudgeDay("grounded", 30, 30)],
        ),
    ]
    assert measured(days, "calls") == 40
    assert measured(days, "spend_usd") == 6.0
    assert measured(days, "escalated_rate") == 0.1
    assert measured(days, "held_rate") == 38 / 40
    assert measured(days, "tool_failure_rate") == 0.25
    assert measured(days, "e2e_median_s") == 2.5
    assert measured(days, "llm_median_s") == 1.25
    assert measured([SeriesDay(day=date(2026, 10, 7))], "held_rate") is None

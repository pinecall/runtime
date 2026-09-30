"""Tests for a hosted app while it runs: stopped and started, its logs, the time it served."""

from datetime import UTC, date, datetime, timedelta

import pytest

from pinecall.domain.errors import NotFound
from pinecall.postgres.pool import Pool
from pinecall.tenancy.hosted_running import (
    LONGEST_BEAT_S,
    LONGEST_LOGS,
    ask_for_logs,
    keep_logs,
    metered,
    served,
    start_app,
    stop_app,
    unmetered,
)
from pinecall.tenancy.hosting import (
    HostedApp,
    apps_of,
    checked_source,
    hosted_in,
    keep_release,
    open_app,
)
from tests.conftest import postgres
from tests.tenancy.conftest import an_org
from tests.tenancy.test_hosting import PROJECT, VAULT

NOON = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


async def a_running_app(pool: Pool) -> HostedApp:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="production", name="support")
    await open_app(pool, VAULT, app, created_by="m_ana")
    await keep_release(pool, app, checked_source(PROJECT), author="m_ana", note="")
    return app


@postgres
async def test_a_stopped_app_is_listed_stopped_and_the_runner_is_not_told_to_run_it(
    pool: Pool,
) -> None:
    app = await a_running_app(pool)
    await stop_app(pool, app, by="m_ana")
    [listed] = await apps_of(pool, app.org, "production")
    assert listed.stopped
    assert await hosted_in(pool, "production") == []
    await start_app(pool, app)
    [running] = await hosted_in(pool, "production")
    assert not running.stopped


@postgres
async def test_stopping_or_starting_an_app_the_box_does_not_host_is_not_found(pool: Pool) -> None:
    org = await an_org(pool)
    nobody = HostedApp(org=org.id, env="production", name="nobody")
    with pytest.raises(NotFound, match="hosts no app called nobody"):
        await stop_app(pool, nobody, by="m_ana")
    with pytest.raises(NotFound, match="hosts no app called nobody"):
        await start_app(pool, nobody)
    with pytest.raises(NotFound, match="hosts no app called nobody"):
        await ask_for_logs(pool, nobody)


@postgres
async def test_asking_for_logs_answers_the_last_ones_and_tells_the_runner_to_send_them(
    pool: Pool,
) -> None:
    app = await a_running_app(pool)
    first = await ask_for_logs(pool, app)
    [wanted_now] = await hosted_in(pool, "production")
    await keep_logs(pool, app, host="support-r1-abcdef12", lines="connected\n")
    second = await ask_for_logs(pool, app)
    assert (first.lines, first.host, first.at) == ("", None, None)
    assert wanted_now.logs_wanted
    assert (second.lines, second.host) == ("connected\n", "support-r1-abcdef12")
    assert second.at is not None


@postgres
async def test_logs_longer_than_the_ceiling_keep_their_last_part(pool: Pool) -> None:
    app = await a_running_app(pool)
    await keep_logs(pool, app, host="h", lines="x" * LONGEST_LOGS + "the end")
    kept = await ask_for_logs(pool, app)
    assert len(kept.lines) == LONGEST_LOGS
    assert kept.lines.endswith("the end")


@postgres
async def test_time_serving_is_counted_from_the_first_beat_and_capped_across_a_gap(
    pool: Pool,
) -> None:
    app = await a_running_app(pool)
    assert await metered(pool, app, NOON) == 0.0
    assert await metered(pool, app, NOON + timedelta(seconds=5)) == 5.0
    assert await metered(pool, app, NOON + timedelta(minutes=10)) == LONGEST_BEAT_S
    [day] = await served(pool, date(2026, 9, 1), date(2026, 10, 1), org=app.org)
    assert (day.name, day.day, day.seconds) == ("support", date(2026, 9, 30), 5.0 + LONGEST_BEAT_S)


@postgres
async def test_an_app_that_stopped_serving_counts_again_from_its_next_beat(pool: Pool) -> None:
    app = await a_running_app(pool)
    await metered(pool, app, NOON)
    await unmetered(pool, app)
    assert await metered(pool, app, NOON + timedelta(hours=1)) == 0.0


@postgres
async def test_each_utc_day_is_its_own_row_and_the_month_asked_bounds_them(pool: Pool) -> None:
    app = await a_running_app(pool)
    midnight = datetime(2026, 9, 30, 23, 59, 50, tzinfo=UTC)
    await metered(pool, app, midnight)
    await metered(pool, app, midnight + timedelta(seconds=20))
    september = await served(pool, date(2026, 9, 1), date(2026, 10, 1), org=app.org)
    october = await served(pool, date(2026, 10, 1), date(2026, 11, 1), org=app.org)
    assert [row.seconds for row in september] == []
    assert [(row.day, row.seconds) for row in october] == [(date(2026, 10, 1), 20.0)]

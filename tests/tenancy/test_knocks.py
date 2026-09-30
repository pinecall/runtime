"""Knocks counted in Postgres: five a minute per name, whichever gateway each lands on."""

from pinecall.postgres.pool import Pool
from pinecall.tenancy.knocks import WINDOW_S, Throttle
from tests.conftest import postgres


@postgres
async def test_the_sixth_knock_in_a_minute_is_refused_on_any_gateway_and_another_name_is_not(
    pool: Pool,
) -> None:
    here, there = Throttle(pool, 5, lambda: 100.0), Throttle(pool, 5, lambda: 100.0)
    assert [await (here if n % 2 else there).allowed("ana") for n in range(5)] == [True] * 5
    assert not await there.allowed("ana")
    assert not await here.allowed("ana")
    assert await here.allowed("bruno")


@postgres
async def test_the_window_slides_so_a_minute_later_the_name_knocks_again(pool: Pool) -> None:
    now = [100.0]
    throttle = Throttle(pool, 5, lambda: now[0])
    for _ in range(5):
        await throttle.allowed("ana")
    assert not await throttle.allowed("ana")
    now[0] += WINDOW_S + 1
    assert await throttle.allowed("ana")


@postgres
async def test_knocks_past_the_window_leave_the_table(pool: Pool) -> None:
    now = [100.0]
    throttle = Throttle(pool, 5, lambda: now[0])
    for name in range(10):
        await throttle.allowed(f"n{name}")
    now[0] += WINDOW_S + 1
    await throttle.allowed("fresh")
    async with pool.connection() as connection:
        rows = await (await connection.execute("select name from knocks")).fetchall()
    assert [row["name"] for row in rows] == ["fresh"]

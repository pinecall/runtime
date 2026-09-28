"""Tests for the box_settings rows: read what the operator set, write it whole."""

from pinecall.postgres import box_settings
from pinecall.postgres.pool import Pool
from tests.conftest import postgres


@postgres
async def test_a_row_nobody_set_reads_as_none(pool: Pool) -> None:
    async with pool.connection() as connection:
        assert await box_settings.read(connection, "nothing-here") is None


@postgres
async def test_a_row_written_twice_reads_back_the_second_whole(pool: Pool) -> None:
    async with pool.connection() as connection:
        await box_settings.write(connection, "fleets", {"production": "a", "sandbox": "b"})
        await box_settings.write(connection, "fleets", {"production": "c"})
        assert await box_settings.read(connection, "fleets") == {"production": "c"}

"""Tests for the doctor's archive line: off said as off, a stuck spool or a failed copy refused."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from pinecall.cli._archive import (
    SHIPPED_WITHIN_S,
    Archive,
    archive_finding,
    archive_of,
)
from pinecall.postgres.pool import Pool
from tests.conftest import postgres

NOW = datetime(2026, 9, 30, 14, 5, tzinfo=UTC)


KEEPING_UP = Archive(
    mode="on",
    last_archived=NOW - timedelta(seconds=40),
    last_failed=None,
    failed_wal=None,
    waiting=1,
    oldest=NOW - timedelta(seconds=5),
)


def test_a_box_without_a_bucket_is_ok_and_says_archiving_is_off() -> None:
    trouble, state = archive_finding(Archive("off", None, None, None, 0, None), NOW)
    assert trouble is None
    assert state.startswith("off")
    assert "PINECALL_BACKUP_BUCKET" in state


def test_an_archive_keeping_up_says_what_waits_and_when_it_last_spooled() -> None:
    assert archive_finding(KEEPING_UP, NOW) == (
        None,
        "on, 1 waiting to ship, the last spooled 40 s ago",
    )


def test_a_segment_postgres_could_not_spool_is_trouble_until_a_later_one_is() -> None:
    failing = replace(
        KEEPING_UP, last_failed=NOW - timedelta(seconds=10), failed_wal="000000010000000000000009"
    )
    trouble, _ = archive_finding(failing, NOW)
    assert trouble is not None
    assert "000000010000000000000009" in trouble
    recovered = replace(KEEPING_UP, last_failed=NOW - timedelta(seconds=90))
    assert archive_finding(recovered, NOW)[0] is None


def test_a_spool_the_bucket_has_not_emptied_in_five_minutes_is_trouble() -> None:
    stuck = replace(KEEPING_UP, waiting=31, oldest=NOW - timedelta(seconds=SHIPPED_WITHIN_S + 1))
    trouble, _ = archive_finding(stuck, NOW)
    assert trouble is not None
    assert trouble.startswith("31 segments waiting since 2026-09-30 13:59:59Z")
    assert (
        archive_finding(replace(KEEPING_UP, oldest=NOW - timedelta(seconds=SHIPPED_WITHIN_S)), NOW)[
            0
        ]
        is None
    )


# Whatever the server archives with: the laptop's Postgres archives nothing, a cluster's
# operator archives everything; with no spool named, no file of the server is listed.
@postgres
async def test_the_archive_is_the_servers_own_mode_and_no_spool_is_read_unless_named(
    pool: Pool,
) -> None:
    async with pool.connection() as connection:
        row = await (
            await connection.execute("SELECT current_setting('archive_mode') AS mode")
        ).fetchone()
    assert row is not None
    archive = await archive_of(pool, None)
    assert (archive.mode, archive.waiting, archive.oldest) == (row["mode"], 0, None)

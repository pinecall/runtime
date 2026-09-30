"""Tests for who read what: a read written once an hour, per org, of one subject when asked."""

import logging

import pytest

from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool, open_pool
from pinecall.tenancy import reads
from pinecall.tenancy.reads import Read
from tests.conftest import DSN, postgres
from tests.tenancy.conftest import an_org

pytestmark = postgres


async def test_the_same_read_within_the_hour_is_written_once(pool: Pool) -> None:
    org = await an_org(pool)
    for _ in range(3):
        await reads.record(pool, Scope(org.id, "sandbox"), Read("CA_1", "log", "m_ana"))
    await reads.record(pool, Scope(org.id, "sandbox"), Read("CA_1", "recording", "m_ana"))
    await reads.record(pool, Scope(org.id, "sandbox"), Read("CA_1", "log", reads.OPERATOR))
    rows = await reads.of_org(pool, org.id)
    assert sorted((row.what, row.reader) for row in rows) == [
        ("log", "m_ana"),
        ("log", "operator"),
        ("recording", "m_ana"),
    ]


async def test_an_orgs_reads_are_its_own_and_one_subject_is_asked_alone(pool: Pool) -> None:
    org = await an_org(pool)
    other = await an_org(pool, "otra")
    await reads.record(pool, Scope(org.id), Read("CA_1", "log", "m_ana"))
    await reads.record(pool, Scope(org.id), Read("+14155550142", "traceback", reads.OPERATOR))
    await reads.record(pool, Scope(other.id), Read("CA_9", "log", "m_luis"))
    assert [row.subject for row in await reads.of_org(pool, org.id, subject="CA_1")] == ["CA_1"]
    assert {row.subject for row in await reads.of_org(pool, org.id)} == {"CA_1", "+14155550142"}


async def test_a_seat_an_export_and_a_memory_read_are_reads_of_their_own_kind(pool: Pool) -> None:
    org = await an_org(pool)
    for kind in ("listen", "supervise", "export", "memory"):
        await reads.record(pool, Scope(org.id), Read("CA_1", kind, "k_server"))
    kinds = {row.what for row in await reads.of_org(pool, org.id)}
    assert kinds == {"listen", "supervise", "export", "memory"}


# The read it records goes on: the record is said in the log, never raised.
async def test_a_record_that_cannot_be_written_never_fails_the_read(
    schema: str, caplog: pytest.LogCaptureFixture
) -> None:
    closed = await open_pool(DSN, schema=schema, max_size=1)
    await closed.close()
    with caplog.at_level(logging.WARNING):
        await reads.record(closed, Scope("org_1"), Read("CA_1", "log", "m_ana"))
    assert "the read of CA_1 by m_ana went unrecorded" in caplog.text

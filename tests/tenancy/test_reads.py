"""Tests for who read what: a read written once an hour, per org, of one subject when asked."""

from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.tenancy import reads
from pinecall.tenancy.reads import Read
from tests.conftest import postgres
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

"""Tests for a number's traceback: a call kept, a call erased with its record, a dial, no other."""

from pathlib import Path

from pinecall.domain.scope import Scope
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.tenancy import erasure, traceback
from tests.conftest import postgres
from tests.log.conftest import ACall, logged_call
from tests.tenancy.conftest import an_org

pytestmark = postgres

DANA = "+34600111222"
LUIS = "+34600333444"

A_DIAL = """
INSERT INTO dials (org, env, agent, call, dialled, shown, asked_by, refused)
VALUES (%(org)s, 'production', 'agenda', NULL, %(number)s, '+34910000000', 'm_ana', 'quiet_hours')
"""


async def test_a_numbers_calls_are_found_kept_or_erased_and_its_dials_beside_them(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    kept = await logged_call(store, org.id, ACall(caller=DANA))
    erased = await logged_call(store, org.id, ACall(caller=DANA))
    await logged_call(store, org.id, ACall(caller=LUIS))
    await erasure.call(pool, tmp_path, Scope(org.id), erased, by="m_ana")
    async with pool.connection() as connection:
        await connection.execute(A_DIAL, {"org": org.id, "number": DANA})
    found = await traceback.of_number(pool, DANA, 0)
    assert found.number == DANA
    assert {(call.call, call.erased) for call in found.calls} == {(kept, False), (erased, True)}
    record = next(call for call in found.calls if call.erased)
    assert (record.org, record.from_number, record.direction) == (org.id, DANA, "inbound")
    assert [(dial.asked_by, dial.refused) for dial in found.dials] == [("m_ana", "quiet_hours")]


async def test_a_call_with_no_number_leaves_no_record_when_it_is_erased(
    pool: Pool, store: Store, tmp_path: Path
) -> None:
    org = await an_org(pool)
    typed = await logged_call(store, org.id, ACall(channel="web", caller=DANA))
    await erasure.call(pool, tmp_path, Scope(org.id), typed, by="m_ana")
    assert (await traceback.of_number(pool, DANA, 0)).calls == []


async def test_nothing_before_the_day_asked_is_found(pool: Pool, store: Store) -> None:
    org = await an_org(pool)
    await logged_call(store, org.id, ACall(caller=DANA))
    found = await traceback.of_number(pool, DANA, 4_000_000_000)
    assert (found.calls, found.dials) == ([], [])

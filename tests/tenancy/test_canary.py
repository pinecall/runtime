"""Tests for the canary: a version on a share of the calls, set, cleared, read back for a call."""

import time

from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.tenancy import canary
from pinecall.tenancy.canary import ALL_CALLS, Canary, CanarySet, bucket_of
from pinecall.tenancy.orgs import create
from tests.conftest import postgres

AGENT = "recepcion"


def test_a_calls_place_is_the_same_every_time_and_one_of_a_hundred() -> None:
    places = [bucket_of(f"CA_{number}") for number in range(400)]
    assert places == [bucket_of(f"CA_{number}") for number in range(400)]
    assert set(places) <= set(range(ALL_CALLS))
    assert len(set(places)) > ALL_CALLS // 2, "four hundred calls spread over the hundred"


@postgres
async def test_a_canary_set_stands_until_it_is_cleared(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    team = Scope(org.id, "sandbox")
    assert await canary.current(pool, team, AGENT) is None
    await canary.put(
        pool, team, AGENT, CanarySet(Canary(version=4, share=10), "m_ana", time.time(), "try")
    )
    found = await canary.current(pool, team, AGENT)
    assert found is not None
    assert (found.canary, found.author, found.note, found.holder) == (
        Canary(4, 10),
        "m_ana",
        "try",
        "",
    )
    assert await canary.current(pool, Scope(org.id, "production"), AGENT) is None
    await canary.put(pool, team, AGENT, CanarySet(None, "m_ana", time.time()))
    assert await canary.current(pool, team, AGENT) is None


@postgres
async def test_a_call_ran_the_canary_when_its_version_was_the_one_standing_as_it_opened(
    pool: Pool,
) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    team = Scope(org.id, "sandbox")
    before = time.time() - 60
    await canary.put(
        pool, team, AGENT, CanarySet(Canary(version=4, share=10), "m_ana", time.time())
    )
    now = time.time() + 1
    assert await canary.ran_the_canary(pool, team, AGENT, 4, now)
    assert not await canary.ran_the_canary(pool, team, AGENT, 3, now), "the rest's version"
    assert not await canary.ran_the_canary(pool, team, AGENT, 4, before), "opened before it"
    assert not await canary.ran_the_canary(pool, team, AGENT, None, now)
    await canary.put(pool, team, AGENT, CanarySet(Canary(version=4, share=0), "m_ana", time.time()))
    assert not await canary.ran_the_canary(pool, team, AGENT, 4, time.time() + 1)

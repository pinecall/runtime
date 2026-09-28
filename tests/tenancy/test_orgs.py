"""Orgs: made with what the box admits them to, limited per world, judged unless turned off."""

import pytest

from pinecall.domain.errors import Conflict
from pinecall.domain.org import DEFAULT_ORG, Org, Quotas
from pinecall.postgres.pool import Pool
from pinecall.tenancy.admission import Admission, admission, quotas_of, set_admission, set_quotas
from pinecall.tenancy.orgs import create, find, judged, listed, set_judging
from tests.conftest import postgres

TRIAL = Quotas(minutes=30, messages=300, concurrent_calls=1, lends=frozenset({"deepgram"}))
CLOSED = Quotas(minutes=0, messages=0, lends=frozenset())


@postgres
async def test_an_org_is_found_by_its_id_or_its_slug_and_listed_after_the_default(
    pool: Pool,
) -> None:
    first = await create(pool, "clinica-norte", "Clínica Norte")
    second = await create(pool, "northwind", "Northwind")
    assert first.id.startswith("org_")
    assert await find(pool, first.id) == first
    assert await find(pool, "northwind") == second
    assert await find(pool, "nobody") is None
    assert await listed(pool) == [
        Org(id=DEFAULT_ORG, slug=DEFAULT_ORG, name=DEFAULT_ORG),
        first,
        second,
    ]


@postgres
async def test_a_slug_another_org_holds_is_refused_in_a_sentence_that_names_it(pool: Pool) -> None:
    await create(pool, "clinica-norte", "Clínica Norte")
    with pytest.raises(Conflict, match="clinica-norte is taken"):
        await create(pool, "clinica-norte", "Otra")
    assert [org.slug for org in await listed(pool)] == [DEFAULT_ORG, "clinica-norte"]


@postgres
async def test_what_the_box_lends_round_trips_and_null_and_empty_stay_apart(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await set_quotas(pool, org.id, "production", Quotas(lends=None))
    assert (await quotas_of(pool, org.id, "production")).lends is None
    await set_quotas(pool, org.id, "production", Quotas(lends=frozenset()))
    assert (await quotas_of(pool, org.id, "production")).lends == frozenset()


@postgres
async def test_an_org_is_judged_until_somebody_turns_it_off(pool: Pool) -> None:
    org = await create(pool, "clinica-norte", "Clínica Norte")
    assert await judged(pool, org.id)
    await set_judging(pool, org.id, on=False)
    assert not await judged(pool, org.id)
    await set_judging(pool, org.id, on=True)
    assert await judged(pool, org.id)


@postgres
async def test_a_new_org_is_born_with_what_the_box_admits_it_to_in_each_world(pool: Pool) -> None:
    await set_admission(pool, Admission(first={"sandbox": TRIAL, "production": CLOSED}))
    org = await create(pool, "clinica-norte", "Clínica Norte")
    assert await quotas_of(pool, org.id, "sandbox") == TRIAL
    assert await quotas_of(pool, org.id, "production") == CLOSED


@postgres
async def test_one_trial_per_person_gives_a_later_org_what_the_box_says_for_later_ones(
    pool: Pool,
) -> None:
    allowed = Admission(
        first={"sandbox": TRIAL, "production": CLOSED},
        later={"sandbox": CLOSED, "production": CLOSED},
    )
    await set_admission(pool, allowed)
    assert await admission(pool) == allowed
    again = await create(pool, "clinica-sur", "Clínica Sur", already=1)
    assert await quotas_of(pool, again.id, "sandbox") == CLOSED

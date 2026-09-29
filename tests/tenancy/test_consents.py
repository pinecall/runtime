"""Tests for consent and the do-not-call list: the newest fact stands, history kept, the list."""

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.tenancy import consents
from pinecall.tenancy.consents import Given
from tests.conftest import postgres
from tests.tenancy.conftest import an_org

pytestmark = postgres

DANA = "+14155550142"
LUIS = "+14155550143"


async def test_a_number_nobody_wrote_about_is_unknown(pool: Pool) -> None:
    org = await an_org(pool)
    assert await consents.standing_of(pool, Scope(org.id), DANA) == "unknown"
    history = await consents.history(pool, Scope(org.id), DANA)
    assert (history.standing, history.rows) == ("unknown", [])


async def test_the_newest_fact_stands_and_every_one_is_kept(pool: Pool) -> None:
    org = await an_org(pool)
    world = Scope(org.id)
    await consents.give(
        pool, world, DANA, Given("express", "the booking form", "m_ana", text="Yes, call me")
    )
    await consents.give(
        pool, world, DANA, Given("opt_out", "the caller asked the agent", "agent:front-desk")
    )
    assert await consents.standing_of(pool, world, DANA) == "opted_out"
    await consents.give(
        pool, world, DANA, Given("written", "a signed form", "m_ana", evidence="s3://form-7")
    )
    history = await consents.history(pool, world, DANA)
    assert history.standing == "consented"
    assert [row.kind for row in history.rows] == ["written", "opt_out", "express"]
    assert history.rows[2].text == "Yes, call me"


async def test_a_world_and_an_org_keep_their_own_facts(pool: Pool) -> None:
    org = await an_org(pool)
    other = await an_org(pool, "otra")
    await consents.give(pool, Scope(org.id), DANA, Given("opt_out", "by hand", "m_ana"))
    assert await consents.standing_of(pool, Scope(org.id, "sandbox"), DANA) == "unknown"
    assert await consents.standing_of(pool, Scope(other.id), DANA) == "unknown"


async def test_the_list_is_the_numbers_whose_newest_fact_is_an_opt_out_a_page_at_a_time(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    world = Scope(org.id)
    added, refused = await consents.opt_out_many(
        pool,
        world,
        [DANA, LUIS, "+14155550144", "not a number"],
        Given("opt_out", "our list", "m_ana"),
    )
    assert (added, refused) == (3, ["not a number"])
    await consents.give(pool, world, LUIS, Given("express", "he called back and asked", "m_ana"))
    first = await consents.do_not_call(pool, world, limit=1)
    assert len(first.numbers) == 1
    assert first.next is not None
    rest = await consents.do_not_call(pool, world, after=first.next, limit=10)
    listed = {opted.number for opted in [*first.numbers, *rest.numbers]}
    assert listed == {DANA, "+14155550144"}
    assert rest.next is None


async def test_a_number_that_is_no_number_and_a_bad_cursor_are_refused(pool: Pool) -> None:
    org = await an_org(pool)
    with pytest.raises(DeclarationRefused):
        await consents.give(pool, Scope(org.id), "tomorrow", Given("express", "x", "m_ana"))
    with pytest.raises(DeclarationRefused, match="no cursor"):
        await consents.do_not_call(pool, Scope(org.id), after="yesterday")


async def test_a_call_that_put_its_number_on_the_list_says_so(pool: Pool) -> None:
    org = await an_org(pool)
    given = Given("opt_out", "the caller asked the agent", "agent:front-desk", call="CA_stop")
    await consents.give(pool, Scope(org.id), DANA, given)
    assert await consents.opted_out_on(pool, "CA_stop")
    assert not await consents.opted_out_on(pool, "CA_other")

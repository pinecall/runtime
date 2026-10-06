"""Tests for the routes table, the dispatch a job starts with, and the rooms an agent is in."""

import psycopg
import pytest

from pinecall.channels import routes
from pinecall.channels.routes import RouteWrite
from pinecall.domain.call import Route
from pinecall.domain.names import Env
from pinecall.postgres.pool import Pool
from tests.conftest import postgres

A_NUMBER = "+59829001199"


async def typed(pool: Pool, org: str, env: str = "production", number: str = A_NUMBER) -> None:
    """An operator typed a route."""
    async with pool.connection() as connection:
        await connection.execute(
            "insert into routes (org, number, agent, channel, env) "
            "values (%s, %s, 'agenda', 'phone', %s)",
            (org, number, env),
        )


@postgres
async def test_an_orgs_routes_are_its_own_in_the_world_asked(pool: Pool) -> None:
    await typed(pool, "org_a")
    await typed(pool, "org_a", "sandbox", "+59829001100")
    await typed(pool, "org_b", number="+59829001101")
    found = await routes.of_org(pool, "org_a", "production")
    assert [(item.org, item.number, item.env) for item in found] == [
        ("org_a", A_NUMBER, "production")
    ]


# One org holds a number: the box refuses a second org's row for it.
@postgres
async def test_a_number_one_org_holds_is_refused_to_another_and_read_as_held_elsewhere(
    pool: Pool,
) -> None:
    await typed(pool, "org_a")
    with pytest.raises(psycopg.errors.UniqueViolation):
        await typed(pool, "org_b")
    found = await routes.at(pool, "phone", A_NUMBER)
    assert found is not None
    assert found.org == "org_a"
    assert await routes.held_elsewhere(pool, "org_b", A_NUMBER)
    assert not await routes.held_elsewhere(pool, "org_a", A_NUMBER)


@postgres
async def test_the_box_lists_every_route_by_number(pool: Pool) -> None:
    await typed(pool, "org_a")
    await typed(pool, "org_b", "sandbox", "+59829001100")
    found = await routes.on_the_box(pool)
    assert [(row.route.org, row.route.number) for row in found] == [
        ("org_b", "+59829001100"),
        ("org_a", A_NUMBER),
    ]


@postgres
async def test_a_route_of_the_box_names_the_kind_of_account_its_number_lives_in(
    pool: Pool,
) -> None:
    async with pool.connection() as connection:
        await connection.execute("insert into orgs (id, slug, name) values ('org_a', 'a', 'A')")
        await connection.execute(
            "insert into carriers (org, kind, account, ciphertext) "
            "values ('org_a', 'twilio', 'AC1', 'sealed')"
        )
    await routes.put(pool, a_route(), RouteWrite("imported", account="AC1"))
    await routes.put(pool, a_route("org_b", number="+59829001100"), RouteWrite("typed"))
    found = await routes.on_the_box(pool)
    assert [(row.route.org, row.carrier) for row in found] == [("org_b", None), ("org_a", "twilio")]


# A number the org hooked itself waits for the operator; every other way is approved as written,
# and one approved stays approved however it is written again.
@postgres
async def test_a_hooked_number_waits_for_the_operator_and_stays_approved_once_approved(
    pool: Pool,
) -> None:
    await routes.put(pool, a_route("org_b"), RouteWrite("hooked"))
    await routes.put(pool, a_route("org_b", number="+59829001100"), RouteWrite("bought"))
    found = await routes.records_of(pool, "org_b", "production")
    assert [(row.route.number, row.origin, row.approved) for row in found] == [
        (A_NUMBER, "hooked", False),
        ("+59829001100", "bought", True),
    ]
    assert await routes.is_waiting(pool, "org_b", A_NUMBER)
    assert await routes.approve(pool, "org_b", A_NUMBER, "operator@box.test")
    assert not await routes.approve(pool, "org_b", A_NUMBER, "operator@box.test"), "once"
    await routes.put(pool, a_route("org_b"), RouteWrite("hooked"))
    assert not await routes.is_waiting(pool, "org_b", A_NUMBER)


@postgres
async def test_a_number_nobody_typed_goes_nowhere(pool: Pool) -> None:
    assert await routes.at(pool, "phone", A_NUMBER) is None


def a_route(
    org: str = "org_a", env: Env = "production", *, managed: bool = False, number: str = A_NUMBER
) -> Route:
    """The org's agenda at the number."""
    return Route(org=org, agent="agenda", channel="phone", number=number, env=env, managed=managed)


@postgres
async def test_a_number_added_twice_moves_and_never_doubles(pool: Pool) -> None:
    await routes.put(pool, a_route(), RouteWrite("typed"))
    await routes.put(pool, a_route(env="sandbox"), RouteWrite("typed"))
    assert await routes.of_org(pool, "org_a", "production") == []
    assert [item.env for item in await routes.of_org(pool, "org_a", "sandbox")] == ["sandbox"]


@postgres
async def test_one_number_of_one_org_is_read_whatever_world_it_is_in(pool: Pool) -> None:
    await routes.put(pool, a_route(env="sandbox"), RouteWrite("typed"))
    assert await routes.of_number(pool, "org_a", A_NUMBER) == a_route(env="sandbox")
    assert await routes.of_number(pool, "org_b", A_NUMBER) is None


@postgres
async def test_removing_says_whether_there_was_a_row_to_remove(pool: Pool) -> None:
    await routes.put(pool, a_route(), RouteWrite("typed"))
    assert await routes.remove(pool, "org_a", A_NUMBER)
    assert not await routes.remove(pool, "org_a", A_NUMBER)


@postgres
async def test_a_number_moved_into_the_other_world_answers_there(pool: Pool) -> None:
    await routes.put(pool, a_route(), RouteWrite("typed"))
    assert await routes.moved(pool, "org_a", A_NUMBER, "sandbox")
    assert not await routes.moved(pool, "org_b", A_NUMBER, "sandbox")
    assert (await routes.at(pool, "phone", A_NUMBER)) == a_route(env="sandbox")


@postgres
async def test_the_numbers_the_box_bought_are_counted_per_world_and_the_flag_round_trips(
    pool: Pool,
) -> None:
    await routes.put(pool, a_route(managed=True), RouteWrite("bought"))
    await typed(pool, "org_a", number="+59829001100")
    assert await routes.managed_in(pool, "org_a", "production") == 1
    assert await routes.managed_in(pool, "org_a", "sandbox") == 0
    assert (await routes.of_number(pool, "org_a", A_NUMBER)) == a_route(managed=True)

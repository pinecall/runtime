"""Tests for the routes table, the dispatch a job starts with, and the rooms an agent is in."""

import logging
from dataclasses import replace

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


@postgres
async def test_a_number_two_orgs_typed_answers_in_the_older_and_the_other_is_named(
    pool: Pool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=routes.__name__)
    await typed(pool, "org_a")
    await typed(pool, "org_b")
    found = await routes.at(pool, "phone", A_NUMBER)
    assert found is not None
    assert found.org == "org_a"
    assert "org_b" in caplog.text


@postgres
async def test_the_box_lists_every_route_by_number_and_the_older_row_answers(pool: Pool) -> None:
    await typed(pool, "org_a")
    await typed(pool, "org_b")
    await typed(pool, "org_b", "sandbox", "+59829001100")
    found = await routes.on_the_box(pool)
    assert [(row.route.org, row.route.number, row.answering) for row in found] == [
        ("org_b", "+59829001100", "org_b"),
        ("org_a", A_NUMBER, "org_a"),
        ("org_b", A_NUMBER, "org_a"),
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
    await routes.put(pool, a_route("org_b"), RouteWrite("typed"))
    found = await routes.on_the_box(pool)
    assert [(row.route.org, row.carrier) for row in found] == [("org_a", "twilio"), ("org_b", None)]


@postgres
async def test_a_row_keeps_how_it_was_written_and_the_org_reads_who_answers_its_number(
    pool: Pool,
) -> None:
    await typed(pool, "org_a")
    await routes.put(pool, a_route("org_b"), RouteWrite("hooked"))
    await routes.put(pool, replace(a_route("org_b"), number="+59829001100"), RouteWrite("bought"))
    found = await routes.records_of(pool, "org_b", "production")
    assert [(row.route.number, row.origin, row.answering) for row in found] == [
        (A_NUMBER, "hooked", "org_a"),
        ("+59829001100", "bought", "org_b"),
    ]
    kept = await routes.record_of(pool, "org_a", A_NUMBER)
    assert kept is not None
    assert (kept.origin, kept.answering) == ("typed", "org_a")


@postgres
async def test_a_number_nobody_typed_goes_nowhere(pool: Pool) -> None:
    assert await routes.at(pool, "phone", A_NUMBER) is None


def a_route(org: str = "org_a", env: Env = "production", *, managed: bool = False) -> Route:
    """The org's agenda at the number."""
    return Route(
        org=org, agent="agenda", channel="phone", number=A_NUMBER, env=env, managed=managed
    )


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

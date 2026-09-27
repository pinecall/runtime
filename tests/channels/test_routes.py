"""Tests for the routes table, the dispatch a job starts with, and the rooms an agent is in."""

import json
import logging

import pytest
from livekit import api

from pinecall.channels import routes
from pinecall.channels.routes import Dialling, Dispatch
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.types import Env, Route
from pinecall.postgres.pool import Pool
from tests.conftest import postgres
from tests.fakes import Server

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
    assert [(one.org, one.number, one.env) for one in found] == [("org_a", A_NUMBER, "production")]


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
async def test_a_number_nobody_typed_goes_nowhere(pool: Pool) -> None:
    assert await routes.at(pool, "phone", A_NUMBER) is None


def a_route(org: str = "org_a", env: Env = "production", *, managed: bool = False) -> Route:
    """The org's agenda at the number."""
    return Route(
        org=org, agent="agenda", channel="phone", number=A_NUMBER, env=env, managed=managed
    )


@postgres
async def test_a_number_added_twice_moves_and_never_doubles(pool: Pool) -> None:
    await routes.put(pool, a_route(), account=None)
    await routes.put(pool, a_route(env="sandbox"), account=None)
    assert await routes.of_org(pool, "org_a", "production") == []
    assert [one.env for one in await routes.of_org(pool, "org_a", "sandbox")] == ["sandbox"]


@postgres
async def test_one_number_of_one_org_is_read_whatever_world_it_is_in(pool: Pool) -> None:
    await routes.put(pool, a_route(env="sandbox"), account=None)
    assert await routes.of_number(pool, "org_a", A_NUMBER) == a_route(env="sandbox")
    assert await routes.of_number(pool, "org_b", A_NUMBER) is None


@postgres
async def test_removing_says_whether_there_was_a_row_to_remove(pool: Pool) -> None:
    await routes.put(pool, a_route(), account=None)
    assert await routes.remove(pool, "org_a", A_NUMBER)
    assert not await routes.remove(pool, "org_a", A_NUMBER)


@postgres
async def test_a_number_moved_into_the_other_world_answers_there(pool: Pool) -> None:
    await routes.put(pool, a_route(), account=None)
    assert await routes.moved(pool, "org_a", A_NUMBER, "sandbox")
    assert not await routes.moved(pool, "org_b", A_NUMBER, "sandbox")
    assert (await routes.at(pool, "phone", A_NUMBER)) == a_route(env="sandbox")


@postgres
async def test_the_numbers_the_box_bought_are_counted_per_world_and_the_flag_round_trips(
    pool: Pool,
) -> None:
    await routes.put(pool, a_route(managed=True), account=None)
    await typed(pool, "org_a", number="+59829001100")
    assert await routes.managed_in(pool, "org_a", "production") == 1
    assert await routes.managed_in(pool, "org_a", "sandbox") == 0
    assert (await routes.of_number(pool, "org_a", A_NUMBER)) == a_route(managed=True)


def test_a_dispatch_travels_as_compact_json_without_what_was_not_said() -> None:
    dispatch = Dispatch(agent="agenda", org="org_a", env="sandbox", holder="m_1")
    said = routes.written(dispatch)
    assert json.loads(said) == {
        "agent": "agenda",
        "org": "org_a",
        "env": "sandbox",
        "holder": "m_1",
    }
    assert routes.read_dispatch(said) == dispatch


def test_an_outbound_dispatch_carries_its_leg() -> None:
    leg = Dialling(trunk="ST_1", to="+59899000001", shown=A_NUMBER, max_duration_s=600)
    dispatch = Dispatch(agent="agenda", direction="outbound", dial=leg)
    assert routes.read_dispatch(routes.written(dispatch)).dial == leg


@pytest.mark.parametrize("metadata", ["", "not json", "[1, 2]", '{"env": "moon"}'])
def test_metadata_that_is_no_dispatch_reads_as_none(metadata: str) -> None:
    assert routes.read_dispatch(metadata) == Dispatch()


def test_a_visitors_room_names_its_worlds_fleet_and_the_dispatch() -> None:
    config = routes.room_dispatch("pinecall-sandbox", Dispatch(agent="agenda", scope="talk"))
    (sent,) = config.agents
    assert sent.agent_name == "pinecall-sandbox"
    assert json.loads(sent.metadata) == {"agent": "agenda", "scope": "talk"}


@pytest.mark.parametrize(
    "said", [{"agents": [{"agentName": "agenda"}]}, {"agents": [{"agent_name": "agenda"}]}]
)
def test_a_livekit_client_names_the_agent_in_either_spelling(said: dict[str, object]) -> None:
    assert routes.client_named_agent(said) == "agenda"


def test_a_room_config_that_is_no_room_config_is_refused_in_the_parsers_words() -> None:
    with pytest.raises(DeclarationRefused, match="RoomConfiguration"):
        routes.client_named_agent({"agents": "nope"})


async def test_only_a_room_an_agent_is_in_is_a_live_call() -> None:
    server = Server()
    server.rooms.standing = {"call_a": True, "call_b": False}
    served = await routes.rooms_with_an_agent(server, ["call_a", "call_b", "call_c"])
    assert served == {"call_a"}
    await server.aclose()


async def test_a_room_closed_is_gone_whoever_was_in_it() -> None:
    server = Server()
    server.rooms.standing = {"call_a": True}
    await routes.room_closed(server, "call_a")
    assert server.rooms.standing == {}
    assert isinstance(server.rooms.asked[-1], api.DeleteRoomRequest)
    await server.aclose()

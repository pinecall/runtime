"""Tests for the dispatch that sends a call to its world's fleet, and the rooms an agent is in."""

import json

import pytest
from livekit import api

from pinecall.channels import rooms
from pinecall.channels.rooms import Dialling, Dispatch
from pinecall.domain.errors import DeclarationRefused
from tests.channels.test_routes import A_NUMBER
from tests.fakes.livekit import Server


def test_a_dispatch_travels_as_compact_json_without_what_was_not_said() -> None:
    carried = Dispatch(agent="agenda", org="org_a", env="sandbox", holder="m_1")
    data = rooms.written(carried)
    assert json.loads(data) == {
        "agent": "agenda",
        "org": "org_a",
        "env": "sandbox",
        "holder": "m_1",
    }
    assert rooms.read_dispatch(data) == carried


def test_an_outbound_dispatch_carries_its_leg() -> None:
    leg = Dialling(trunk="ST_1", to="+59899000001", shown=A_NUMBER, max_duration_s=600)
    carried = Dispatch(agent="agenda", direction="outbound", dial=leg)
    assert rooms.read_dispatch(rooms.written(carried)).dial == leg


@pytest.mark.parametrize("metadata", ["", "not json", "[1, 2]", '{"env": "moon"}'])
def test_metadata_that_is_no_dispatch_reads_as_none(metadata: str) -> None:
    assert rooms.read_dispatch(metadata) == Dispatch()


def test_a_visitors_room_names_its_worlds_fleet_and_the_dispatch() -> None:
    config = rooms.room_dispatch("pinecall-sandbox", Dispatch(agent="agenda", scope="talk"))
    (sent,) = config.agents
    assert sent.agent_name == "pinecall-sandbox"
    assert json.loads(sent.metadata) == {"agent": "agenda", "scope": "talk"}


@pytest.mark.parametrize(
    "data", [{"agents": [{"agentName": "agenda"}]}, {"agents": [{"agent_name": "agenda"}]}]
)
def test_a_livekit_client_names_the_agent_in_either_spelling(data: dict[str, object]) -> None:
    assert rooms.client_named_agent(data) == "agenda"


def test_a_room_config_that_is_no_room_config_is_refused_in_the_parsers_words() -> None:
    with pytest.raises(DeclarationRefused, match="RoomConfiguration"):
        rooms.client_named_agent({"agents": "nope"})


async def test_only_a_room_an_agent_is_in_is_a_live_call() -> None:
    server = Server()
    server.rooms.existing = {"call_a": True, "call_b": False}
    served = await rooms.rooms_with_an_agent(server, ["call_a", "call_b", "call_c"])
    assert served == {"call_a"}
    await server.aclose()


async def test_a_room_closed_is_gone_whoever_was_in_it() -> None:
    server = Server()
    server.rooms.existing = {"call_a": True}
    await rooms.room_closed(server, "call_a")
    assert server.rooms.existing == {}
    assert isinstance(server.rooms.requests[-1], api.DeleteRoomRequest)
    await server.aclose()

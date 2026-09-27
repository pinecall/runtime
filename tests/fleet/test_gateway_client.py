"""Tests for the worker's client, knocking on a real gateway."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from datetime import date

import pytest
from pydantic import TypeAdapter
from websockets.asyncio.client import ClientConnection

from pinecall.channels import telephony
from pinecall.channels.telephony import Import, SipPeer
from pinecall.domain.errors import GatewayRefused
from pinecall.domain.types import CallContext, Corner, Route, new_call_id
from pinecall.fleet.gateway_client import Gateway, again, away, gateway_at, server_sent, waits
from pinecall.providers.keys import Pipeline
from pinecall.wire.rest import Heartbeat, Opening, Sealing
from tests.conftest import AGENT, Knocking, postgres, said_until, sent

A_NUMBER = "+59829001199"


def the_fleet(knocking: Knocking) -> Gateway:
    """The gateway as the sandbox fleet's worker knocks on it."""
    return gateway_at(knocking.url, knocking.fleet["sandbox"])


def a_call(knocking: Knocking) -> CallContext:
    """A sandbox call of the org's agent through the widget."""
    return CallContext(
        call=new_call_id(),
        channel="web",
        direction="inbound",
        caller="web_1",
        route=Route(org=knocking.org.id, agent=AGENT, channel="web", env="sandbox"),
        today=date(2026, 9, 28),
    )


async def held(knocking: Knocking) -> ClientConnection:
    """An app holding the agent in the sandbox."""
    socket = await knocking.socket("/v1/apps", knocking.app["sandbox"])
    await sent(socket, "agent.register", {"routes": []})
    await said_until(socket, "agent.registered")
    return socket


@postgres
async def test_the_agent_and_its_stages_come_back_as_the_domain_holds_them(
    gateway: Knocking,
) -> None:
    socket = await held(gateway)
    client = the_fleet(gateway)
    corner = Corner(gateway.org.id, "sandbox")
    config = await client.agent(AGENT, corner)
    stages = TypeAdapter(Pipeline).validate_python(await client.stages(AGENT, corner))
    assert config.slug == AGENT
    assert stages.lent == ["acme"]
    await client.aclose()
    await socket.close()


@postgres
async def test_a_call_opened_writes_and_seals_through_the_client(gateway: Knocking) -> None:
    client = the_fleet(gateway)
    context = a_call(gateway)
    opened = await client.open(Opening(agent=AGENT, context=context))
    written = await client.append(context.call, "custom", {"name": "x", "data": {}})
    await client.sealed(context.call, Sealing(usage=[], outcome="done"))
    assert opened.seconds_left is None
    assert written.seq == 2
    assert await gateway.box.logs.store.sealed(context.call)
    assert context.call not in client.opened
    await client.aclose()


@postgres
async def test_a_gateway_that_forgot_the_call_is_told_it_again_and_the_entry_lands(
    gateway: Knocking,
) -> None:
    client = the_fleet(gateway)
    context = a_call(gateway)
    await client.open(Opening(agent=AGENT, context=context))
    gateway.box.live.close(context.call)
    gateway.box.logs.forget(context.call)
    written = await client.append(context.call, "custom", {"name": "x", "data": {}})
    assert written.seq == 2
    await client.aclose()


@postgres
async def test_a_code_nobody_issued_is_a_no_and_not_a_refusal(gateway: Knocking) -> None:
    client = the_fleet(gateway)
    context = a_call(gateway)
    await client.open(Opening(agent=AGENT, context=context))
    assert not await client.claim(context.call, "1234")
    await client.aclose()


@postgres
async def test_the_apps_commands_reach_the_worker_in_order(gateway: Knocking) -> None:
    socket = await held(gateway)
    client = the_fleet(gateway)
    context = a_call(gateway)
    await client.open(Opening(agent=AGENT, context=context))
    await sent(socket, "agent.say", {"text": "uno"}, call=context.call)
    await sent(socket, "agent.say", {"text": "dos"}, call=context.call)
    heard: list[str] = []
    async with contextlib.aclosing(client.commands(context.call)) as commands:
        async for command in commands:
            heard.append(str(command.data["text"]))
            if len(heard) == 2:
                break
    assert heard == ["uno", "dos"]
    await client.aclose()
    await socket.close()


@postgres
async def test_a_heartbeat_says_the_fleet_is_open(gateway: Knocking) -> None:
    client = the_fleet(gateway)
    beat = Heartbeat(
        fleet="pinecall-sandbox", worker="w1", active=0, max_jobs=4, load=0.1, draining=False
    )
    standing = await client.heartbeat(beat)
    assert not standing.cordoned
    assert not await client.fleet_is_full("pinecall-sandbox")
    await client.aclose()


@postgres
async def test_a_refusal_names_the_request_refused_and_its_status(gateway: Knocking) -> None:
    client = the_fleet(gateway)
    with pytest.raises(GatewayRefused, match="/v1/agents/nobody/config") as refused:
        await client.agent("nobody", Corner(gateway.org.id, "sandbox"))
    assert refused.value.answered == 404
    await client.aclose()


async def test_a_gateway_that_is_not_there_is_a_refusal_and_never_a_traceback() -> None:
    client = gateway_at("http://127.0.0.1:9", "pc_test_x")
    with pytest.raises(GatewayRefused) as refused:
        await client.fleet_is_full("pinecall")
    assert refused.value.answered is None
    await client.aclose()


def test_the_waits_double_to_the_cap() -> None:
    pauses = waits()
    assert [next(pauses) for _ in range(6)] == [0.5, 1.0, 2.0, 4.0, 5.0, 5.0]


def test_away_is_unreachable_or_a_5xx_and_a_4xx_is_an_answer() -> None:
    assert away(GatewayRefused("x"))
    assert away(GatewayRefused("x", answered=503))
    assert not away(GatewayRefused("x", answered=404))


async def test_an_attempt_is_asked_again_while_the_gateway_is_away() -> None:
    tries: list[int] = []

    async def attempt() -> str:
        tries.append(1)
        if len(tries) < 2:
            raise GatewayRefused("away", answered=502)
        return "done"

    assert await again(attempt, None, "x") == "done"
    assert len(tries) == 2


async def test_a_4xx_is_final_and_not_asked_again() -> None:
    tries: list[int] = []

    async def attempt() -> str:
        tries.append(1)
        raise GatewayRefused("no", answered=409)

    with pytest.raises(GatewayRefused):
        await again(attempt, None, "x")
    assert len(tries) == 1


async def test_an_attempt_with_a_deadline_stops_asking_at_it() -> None:
    async def attempt() -> str:
        raise GatewayRefused("away")

    with pytest.raises(GatewayRefused):
        await asyncio.wait_for(again(attempt, 0.1, "x"), 2)


async def test_a_stream_is_read_as_each_messages_data() -> None:
    async def lines() -> AsyncIterator[str]:
        for line in (": ping", "", "id: 1", "event: x", 'data: {"a": 1}', "", 'data: {"b": 2}', ""):
            yield line

    assert [one async for one in server_sent(lines())] == [{"a": 1}, {"b": 2}]


@postgres
async def test_a_leg_is_told_its_trunk_inline_and_a_refusal_is_the_gateways_sentence(
    gateway: Knocking,
) -> None:
    fleet = the_fleet(gateway)
    corner = Corner(gateway.org.id, "sandbox")
    with pytest.raises(GatewayRefused, match="answers at no phone number"):
        await fleet.leg(AGENT, corner, to="+34910000000", call="call_1", shown=None)
    peer = SipPeer.model_validate(
        {
            "username": "pbx",
            "password": "a peer's password",
            "addresses": ["203.0.113.0/24"],
            "outbound_host": "sip.pbx.test",
            "outbound_transport": "tls",
        }
    )
    await telephony.bring(gateway.box.exchange, gateway.org.id, peer)
    wanted = Import(corner, AGENT, A_NUMBER, account="pbx")
    await telephony.import_number(gateway.box.exchange, wanted)
    leg = await fleet.leg(AGENT, corner, to="+34910000000", call="call_1", shown=A_NUMBER)
    await fleet.aclose()
    assert (leg.hostname, leg.transport, leg.username, leg.shown) == (
        "sip.pbx.test",
        "tls",
        "pbx",
        A_NUMBER,
    )

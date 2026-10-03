"""Tests for the worker's client, knocking on a real gateway."""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from functools import partial

import httpx
import pytest
from opentelemetry import context as otel_context
from opentelemetry import trace
from pydantic import TypeAdapter
from websockets.asyncio.client import ClientConnection

from pinecall.channels.telephony import numbers
from pinecall.channels.telephony.numbers import NumberImport
from pinecall.domain.call import CallContext, Route, new_call_id
from pinecall.domain.errors import GatewayRefused
from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.fleet import client as client_module
from pinecall.fleet.client import (
    GatewayClient,
    affine,
    again,
    away,
    gateway_at,
    server_sent,
    traced,
    waits,
)
from pinecall.providers.credentials import Pipeline
from pinecall.session.call import Writing
from pinecall.tenancy import carriers
from pinecall.tenancy.carriers import SipPeer
from pinecall.wire.events import Custom
from pinecall.wire.rest.calls import CallbackRequest, OpenCallRequest, SealCallRequest
from pinecall.wire.rest.fleet import HeartbeatRequest
from tests.conftest import AGENT, Knocking, postgres, received_until, sent

A_NUMBER = "+59829001199"


def fleet_client(knocking: Knocking) -> GatewayClient:
    """The gateway as the sandbox fleet's worker knocks on it."""
    return gateway_at(knocking.url, knocking.fleet["sandbox"])


def a_call(knocking: Knocking, call: str | None = None) -> CallContext:
    """A sandbox call of the org's agent through the widget, a new one unless named."""
    return CallContext(
        call=call or new_call_id(),
        channel="web",
        direction="inbound",
        caller="web_1",
        route=Route(org=knocking.org.id, agent=AGENT, channel="web", env="sandbox"),
        today=date(2026, 9, 28),
    )


async def holding_app(knocking: Knocking) -> ClientConnection:
    """An app holding the agent in the sandbox."""
    socket = await knocking.socket("/v1/apps", knocking.app["sandbox"])
    await sent(socket, "agent.register", {"routes": []})
    await received_until(socket, "agent.registered")
    return socket


@postgres
async def test_the_agent_and_its_stages_come_back_as_the_domain_holds_them(
    knocking: Knocking,
) -> None:
    socket = await holding_app(knocking)
    client = fleet_client(knocking)
    scope = Scope(knocking.org.id, "sandbox")
    config = await client.agent(AGENT, scope, call="CA_not_opened_yet")
    stages = TypeAdapter(Pipeline).validate_python(
        await client.stages(AGENT, scope, call="CA_not_opened_yet")
    )
    assert config.slug == AGENT
    assert stages.lent == ["acme"]
    await client.aclose()
    await socket.close()


@postgres
async def test_a_call_opened_writes_and_seals_through_the_client(knocking: Knocking) -> None:
    client = fleet_client(knocking)
    context = a_call(knocking)
    opened = await client.open(OpenCallRequest(agent=AGENT, context=context))
    writing = writing_on(client, context.call)
    writing.open()
    written = await writing.write("custom", custom("x"))
    await writing.close(5)
    await client.sealed(context.call, SealCallRequest(usage=[], outcome="done"))
    assert opened.seconds_left is None
    assert written.seq == 2
    assert await knocking.gateway.logs.store.sealed(context.call)
    assert context.call not in client.opened
    await client.aclose()


# A gateway that died with the open or the seal in flight: the worker asks again, the call is one.
@postgres
async def test_an_open_asked_again_is_the_same_call_and_a_seal_asked_again_is_done(
    knocking: Knocking,
) -> None:
    client = fleet_client(knocking)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    await client.sealed(context.call, SealCallRequest(usage=[], outcome="done"))
    client.opened[context.call] = OpenCallRequest(agent=AGENT, context=context)
    await client.sealed(context.call, SealCallRequest(usage=[], outcome="done"))
    kept = await knocking.gateway.logs.store.whole(context.call)
    assert [entry.type for entry in kept].count("call.ringing") == 1
    assert [entry.type for entry in kept].count("call.summary") == 1
    assert context.call not in client.opened
    await client.aclose()


# The seal is held from here as its memory and its judges hold it: longer than a request waits.
@postgres
async def test_a_seal_slower_than_a_request_is_waited_for_and_never_asked_twice(
    knocking: Knocking,
) -> None:
    headers = {"Authorization": f"Bearer {knocking.fleet['sandbox']}"}
    hasty = httpx.AsyncClient(base_url=knocking.url, headers=headers, timeout=0.2)
    client = GatewayClient(hasty)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    served = knocking.gateway.live.calls[context.call]
    await served.sealing.acquire()
    sealing = asyncio.create_task(
        client.sealed(context.call, SealCallRequest(usage=[], outcome="done"))
    )
    await asyncio.sleep(0.6)
    served.sealing.release()
    await sealing
    kinds = [item.type for item in await knocking.gateway.logs.store.whole(context.call)]
    assert kinds.count("call.summary") == 1
    assert await knocking.gateway.logs.store.sealed(context.call)
    await client.aclose()


@dataclass
class LosingTheFirstBatchAnswer:
    """The real transport, but the answer to the first batch is lost after the gateway took it."""

    real: httpx.AsyncHTTPTransport = field(default_factory=httpx.AsyncHTTPTransport)
    lost: int = 0

    async def handled(self, request: httpx.Request) -> httpx.Response:
        """Pass the request on; drop the first answer to a batch as a broken connection would."""
        answer = await self.real.handle_async_request(request)
        if not request.url.path.endswith("/entries") or self.lost:
            return answer
        await answer.aread()
        await answer.aclose()
        self.lost += 1
        raise httpx.ReadError("the answer was lost on the way back", request=request)


def losing_client(knocking: Knocking, losing: LosingTheFirstBatchAnswer) -> GatewayClient:
    """The sandbox fleet's client on a transport that loses the first batch's answer."""
    headers = {"Authorization": f"Bearer {knocking.fleet['sandbox']}"}
    transport = httpx.MockTransport(losing.handled)
    client = GatewayClient(
        httpx.AsyncClient(base_url=knocking.url, headers=headers, transport=transport)
    )
    # The batches by request, as a client that found no socket door sends them: the transport
    # loses answers, and a socket's lost answer is test_a_socket_lost_mid_call's.
    client.doorless = True
    return client


def writing_on(client: GatewayClient, call: str) -> Writing:
    """A call's writer wired to the client's batch door, as a worker wires it."""
    return Writing(partial(client.append_many, call), call)


def custom(name: str) -> Custom:
    """An app's own line, named."""
    return Custom(name=name, data={})


@postgres
async def test_a_calls_entries_written_through_its_writer_land_once_and_in_order(
    knocking: Knocking,
) -> None:
    client = fleet_client(knocking)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    writing = writing_on(client, context.call)
    writing.write("custom", custom("uno"))
    writing.write("custom", custom("dos"))
    writing.open()
    await writing.write("custom", custom("tres"))
    await writing.close(5)
    kept = await knocking.gateway.logs.store.whole(context.call)
    assert [entry.data["name"] for entry in kept if entry.type == "custom"] == [
        "uno",
        "dos",
        "tres",
    ]
    assert await knocking.gateway.logs.store.written(context.call) == 3
    assert writing.refused == []
    await client.aclose()


@postgres
async def test_a_calls_batches_go_down_one_socket_until_the_seal_closes_it(
    knocking: Knocking,
) -> None:
    client = fleet_client(knocking)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    writing = writing_on(client, context.call)
    writing.open()
    await writing.write("custom", custom("uno"))
    stream = client.streams[context.call]
    assert stream.socket is not None, "the first batch opened the call's socket"
    await writing.write("custom", custom("dos"))
    assert client.streams[context.call] is stream, "the second went down the same socket"
    await writing.close(5)
    await client.sealed(context.call, SealCallRequest(usage=[], outcome="done"))
    assert context.call not in client.streams
    assert stream.socket is None
    assert await knocking.gateway.logs.store.written(context.call) == 2
    await client.aclose()


@postgres
async def test_a_socket_lost_mid_call_is_opened_again_and_the_batch_lands_once(
    knocking: Knocking,
) -> None:
    client = fleet_client(knocking)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    writing = writing_on(client, context.call)
    writing.open()
    await writing.write("custom", custom("uno"))
    first = client.streams[context.call].socket
    assert first is not None
    await first.close()
    entry = await writing.write("custom", custom("dos"))
    await writing.close(5)
    second = client.streams[context.call].socket
    assert second is not None
    assert second is not first
    kept = await knocking.gateway.logs.store.whole(context.call)
    customs = [item for item in kept if item.type == "custom"]
    assert [item.data["name"] for item in customs] == ["uno", "dos"]
    assert entry.seq == customs[-1].seq
    assert writing.refused == []
    await client.aclose()


@postgres
async def test_a_gateway_with_no_socket_door_takes_every_batch_by_request(
    knocking: Knocking, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(client_module, "STREAM_DOOR", "/v1/calls/{call}/no-such-door")
    client = fleet_client(knocking)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    writing = writing_on(client, context.call)
    writing.open()
    await writing.write("custom", custom("uno"))
    assert client.doorless
    assert client.streams == {}
    await writing.write("custom", custom("dos"))
    await writing.close(5)
    assert await knocking.gateway.logs.store.written(context.call) == 2
    assert writing.refused == []
    await client.aclose()


@postgres
async def test_a_batch_whose_answer_was_lost_is_sent_again_and_lands_once(
    knocking: Knocking,
) -> None:
    losing = LosingTheFirstBatchAnswer()
    client = losing_client(knocking, losing)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    writing = writing_on(client, context.call)
    names = ["uno", "dos", "tres"]
    written = [writing.write("custom", custom(name)) for name in names]
    writing.open()
    await writing.close(5)
    kept = await knocking.gateway.logs.store.whole(context.call)
    customs = [entry for entry in kept if entry.type == "custom"]
    assert losing.lost == 1
    assert [entry.data["name"] for entry in customs] == names
    assert [future.result().seq for future in written] == [entry.seq for entry in customs]
    assert await knocking.gateway.logs.store.written(context.call) == 3
    await client.aclose()
    await losing.real.aclose()


@postgres
async def test_a_gateway_that_forgot_the_call_is_told_it_again_and_the_batch_lands(
    knocking: Knocking,
) -> None:
    client = fleet_client(knocking)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    writing = writing_on(client, context.call)
    writing.open()
    await writing.write("custom", custom("uno"))
    knocking.gateway.live.close(context.call)
    knocking.gateway.logs.forget(context.call)
    entry = await writing.write("custom", custom("dos"))
    await writing.close(5)
    kept = await knocking.gateway.logs.store.whole(context.call)
    customs = [item for item in kept if item.type == "custom"]
    assert [item.data["name"] for item in customs] == ["uno", "dos"]
    assert entry.seq == customs[-1].seq
    assert writing.refused == []
    await client.aclose()


@postgres
async def test_a_code_nobody_issued_is_a_no_and_not_a_refusal(knocking: Knocking) -> None:
    client = fleet_client(knocking)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    assert not await client.claim(context.call, "1234")
    await client.aclose()


@postgres
async def test_the_apps_commands_reach_the_worker_in_order(knocking: Knocking) -> None:
    socket = await holding_app(knocking)
    client = fleet_client(knocking)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
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
async def test_a_heartbeat_says_the_fleet_is_open(knocking: Knocking) -> None:
    client = fleet_client(knocking)
    beat = HeartbeatRequest(
        fleet="pinecall-sandbox", worker="w1", active=0, max_jobs=4, load=0.1, draining=False
    )
    existing = await client.heartbeat(beat)
    assert not existing.cordoned
    assert not existing.full
    await client.aclose()


# Every door a worker knocks at before and during a call, as a worker of this release and the
# one before it knock: the fleet's key, the dispatch's scope, the call named once it is opened.
@postgres
async def test_every_request_a_worker_makes_passes_with_the_fleets_key(
    knocking: Knocking,
) -> None:
    socket = await holding_app(knocking)
    client = fleet_client(knocking)
    scope = Scope(knocking.org.id, "sandbox")
    context = a_call(knocking)
    assert await client.routes(scope, number=None, channel="phone") == []
    assert await client.routes(None, number=A_NUMBER, channel="phone") == []
    assert (await client.hold_audio(AGENT, scope)).played == "default"
    assert (await client.rings_for(AGENT, knocking.org.id, "+59899000001")).holder is None
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    writing = writing_on(client, context.call)
    writing.open()
    await writing.write("custom", custom("x"))
    await writing.close(5)
    # The one-entry door the worker before this release writes through.
    single: JsonObject = {"type": "custom", "data": {"name": "y", "data": {}}}
    assert (await client.http.post(f"/v1/calls/{context.call}/events", json=single)).is_success
    state = await client.state(context.call)
    since = [entry.type async for entry in client.since(context.call, 0)]
    wanted = CallbackRequest(agent=AGENT, channel="phone", number="+59899000002", call=context.call)
    await client.callback(wanted)
    await client.sealed(context.call, SealCallRequest(usage=[], outcome="done"))
    tail = [entry.type async for entry in client.tail(context.call, 0)]
    assert state["last_seq"] == 3
    assert since == ["call.ringing", "custom", "custom"]
    assert tail[:3] == ["call.ringing", "custom", "custom"]
    assert tail[-1] == "call.score"
    with pytest.raises(GatewayRefused, match="plays no clip") as refused:
        await client.hold_clip(AGENT, scope)
    assert refused.value.answered == 404
    await client.aclose()
    await socket.close()


@postgres
async def test_a_refusal_names_the_request_refused_and_its_status(knocking: Knocking) -> None:
    client = fleet_client(knocking)
    with pytest.raises(GatewayRefused, match="/v1/agents/nobody/config") as refused:
        await client.agent("nobody", Scope(knocking.org.id, "sandbox"))
    assert refused.value.answered == 404
    await client.aclose()


async def test_a_gateway_that_is_not_there_is_a_refusal_and_never_a_traceback() -> None:
    client = gateway_at("http://127.0.0.1:9", "pc_test_x")
    with pytest.raises(GatewayRefused) as refused:
        await client.heartbeat(
            HeartbeatRequest(
                fleet="pinecall", worker="w1", active=0, max_jobs=4, load=0.0, draining=False
            )
        )
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

    assert [item async for item in server_sent(lines())] == [{"a": 1}, {"b": 2}]


@postgres
async def test_a_leg_is_told_its_trunk_inline_and_a_refusal_is_the_gateways_sentence(
    knocking: Knocking,
) -> None:
    fleet = fleet_client(knocking)
    scope = Scope(knocking.org.id, "sandbox")
    call = a_call(knocking).call
    with pytest.raises(GatewayRefused, match="no call call_1 was opened"):
        await fleet.leg(AGENT, scope, to="+34910000000", call="call_1", shown=None)
    await fleet.open(OpenCallRequest(agent=AGENT, context=a_call(knocking, call)))
    with pytest.raises(GatewayRefused, match="answers at no phone number"):
        await fleet.leg(AGENT, scope, to="+34910000000", call=call, shown=None)
    peer = SipPeer.model_validate(
        {
            "username": "pbx",
            "password": "a peer's password",
            "addresses": ["203.0.113.0/24"],
            "outbound_host": "sip.pbx.test",
            "outbound_transport": "tls",
        }
    )
    await carriers.put_carrier(
        knocking.gateway.connections.pool, knocking.gateway.connections.vault, knocking.org.id, peer
    )
    wanted = NumberImport(scope, AGENT, A_NUMBER, account="pbx")
    await numbers.import_number(knocking.gateway.connections, wanted)
    leg = await fleet.leg(AGENT, scope, to="+34910000000", call=call, shown=A_NUMBER)
    await fleet.aclose()
    assert (leg.hostname, leg.transport, leg.username, leg.shown) == (
        "sip.pbx.test",
        "tls",
        "pbx",
        A_NUMBER,
    )


@postgres
async def test_a_recording_key_is_the_calls_one_and_a_gateway_of_before_gives_none(
    knocking: Knocking,
) -> None:
    client = fleet_client(knocking)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    first = await client.recording_key(context.call)
    assert first is not None
    assert await client.recording_key(context.call) == first
    await client.aclose()
    older = httpx.MockTransport(lambda _: httpx.Response(404, json={"detail": "Not Found"}))
    before = GatewayClient(httpx.AsyncClient(base_url="http://gateway.test", transport=older))
    assert await before.recording_key(context.call) is None
    await before.aclose()


# A request carries the worker's current span as traceparent, and nothing when nothing traces.
def test_the_workers_trace_rides_its_requests_to_the_gateway() -> None:
    assert traced() == {}
    context = trace.set_span_in_context(
        trace.NonRecordingSpan(
            trace.SpanContext(
                trace_id=0x1234, span_id=0x5678, is_remote=False, trace_flags=trace.TraceFlags(1)
            )
        )
    )
    token = otel_context.attach(context)
    try:
        carried = traced()
    finally:
        otel_context.detach(token)
    assert carried == {"traceparent": f"00-{0x1234:032x}-{0x5678:016x}-01"}


def test_a_calls_doors_name_the_call_for_the_balancer_and_the_others_do_not() -> None:
    assert affine("/v1/calls/CA_1/entries") == {"Pinecall-Call": "CA_1"}
    assert affine("/v1/calls/CA_1") == {"Pinecall-Call": "CA_1"}
    assert affine("/v1/calls") == {}
    assert affine("/v1/fleet/heartbeat") == {}

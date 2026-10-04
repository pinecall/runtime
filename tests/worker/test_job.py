"""Tests for how a job finds its call, and for the entries it writes outside its session."""

import time
from functools import partial

import pytest
from livekit import api, rtc
from livekit.protocol.sip import CreateSIPParticipantRequest

from pinecall.channels.rooms import Dispatch, read_dispatch
from pinecall.channels.telephony.hand_over import HEADER_OF, HOLDER, ORG, USERNAME, password_of
from pinecall.domain.agent import AgentConfig
from pinecall.domain.call import Route
from pinecall.domain.errors import GatewayRefused, NotFound
from pinecall.domain.names import THE_WIDGET, JsonObject
from pinecall.domain.scope import Scope
from pinecall.providers.build import Running
from pinecall.session.call import Call, Platform, ToolUse
from pinecall.session.room import CALLER_NUMBER, DIALLED_NUMBER
from pinecall.session.text import text_session
from pinecall.wire.events import CallEnded
from pinecall.wire.frames import Command
from pinecall.wire.metrics import ModelUsage
from pinecall.wire.parts import EndedBy, EndReason, PlatformTool, ToolResult
from pinecall.wire.rest.agents import RingHandoff
from pinecall.wire.rest.calls import OpenCallRequest, OpenCallResponse
from pinecall.wire.rest.numbers import LegTrunk
from pinecall.worker._job import (
    Arrival,
    applied,
    arrival_of,
    end_reason_of,
    ended_and_sealed,
    handed_on,
    may_be_a_developers,
    named_by,
    opening_of,
    resolve,
    room_over,
    writer_of,
)
from tests.conftest import AGENT, Knocking, postgres
from tests.fakes.acme import ACME, seat
from tests.fakes.livekit import A_SECRET, Room, Server
from tests.fleet.test_client import LosingTheFirstBatchAnswer, a_call, losing_client

NUMBER = "+15550100"
CALLER = "+15550199"
SIP = rtc.ParticipantKind.PARTICIPANT_KIND_SIP

A_PHONE = Arrival(caller=CALLER, channel="phone", direction="inbound", number=NUMBER)
A_PAGE = Arrival(caller="room_1", channel=THE_WIDGET, direction="inbound")
AT_THE_NUMBER = Route(org="org_a", agent="front", channel="phone", number=NUMBER)
IN_THE_SANDBOX = Route(org="org_a", agent="front", channel="phone", number=NUMBER, env="sandbox")

# A developer's sandbox copy, and what a ring from their own phone is sent on to it with.
MOVED = Dispatch(
    agent="front",
    org="org_a",
    env="sandbox",
    holder="m_ana",
    caller=CALLER,
    diverted_from="production",
)
TO_THE_SANDBOX = LegTrunk(
    hostname="sip.sandbox.box.test",
    transport="udp",
    username=USERNAME,
    password=password_of(A_SECRET),
    shown=CALLER,
)


async def test_a_sip_leg_says_who_calls_and_which_number_they_dialled() -> None:
    leg = seat("sip_1", kind=SIP, attributes={CALLER_NUMBER: CALLER, DIALLED_NUMBER: NUMBER})
    arrived = await arrival_of(Dispatch(), Room("call_1", leg))
    assert arrived == A_PHONE


async def test_a_dispatch_naming_its_agent_does_not_wait_for_a_leg() -> None:
    arrived = await arrival_of(Dispatch(agent="front", caller="visitor"), Room("call_1"))
    assert arrived == Arrival(caller="visitor", channel=THE_WIDGET, direction="inbound")


async def test_an_outbound_job_is_a_phone_call_it_places_itself() -> None:
    arrived = await arrival_of(Dispatch(direction="outbound", caller=CALLER), Room("call_1"))
    assert arrived == Arrival(caller=CALLER, channel="phone", direction="outbound")


def test_a_number_dialled_finds_the_route_that_answers_it() -> None:
    assert resolve(Dispatch(), A_PHONE, [AT_THE_NUMBER], "fallback") == AT_THE_NUMBER


def test_a_number_nobody_answers_is_refused_rather_than_sent_to_the_default() -> None:
    with pytest.raises(NotFound, match="nobody answers"):
        resolve(Dispatch(), A_PHONE, [], "fallback")


def test_with_no_agent_and_no_number_only_the_workers_default_is_left() -> None:
    with pytest.raises(NotFound, match="no default agent"):
        resolve(Dispatch(), A_PAGE, [], None)


def test_the_dispatchs_agent_wins_over_the_number() -> None:
    other = Route(org="org_a", agent="sales", channel="phone", number="+15550111")
    dispatch = Dispatch(agent="sales")
    assert resolve(dispatch, A_PHONE, [AT_THE_NUMBER, other], None) == other


def test_every_agent_answers_on_the_widget_without_a_stored_route() -> None:
    dispatch = Dispatch(agent="front", org="org_a", env="sandbox")
    found = resolve(dispatch, A_PAGE, [], None)
    assert found == Route(org="org_a", agent="front", channel=THE_WIDGET, env="sandbox")


def test_an_agent_with_no_door_on_the_channel_names_where_it_looked() -> None:
    with pytest.raises(NotFound, match="org_a/production"):
        resolve(Dispatch(agent="sales"), A_PHONE, [AT_THE_NUMBER], None)


def test_a_ring_handed_to_the_sandbox_keeps_the_production_number_it_dialled() -> None:
    dispatch = Dispatch(agent="front", org="org_a", env="sandbox", diverted_from="production")
    assert resolve(dispatch, A_PHONE, [], None) == IN_THE_SANDBOX


async def test_a_ring_handed_over_waits_for_its_leg_to_say_the_number_it_dialled() -> None:
    leg = seat("sip_1", kind=SIP, attributes={CALLER_NUMBER: CALLER, DIALLED_NUMBER: NUMBER})
    arrived = await arrival_of(MOVED, Room("call_1", leg))
    assert arrived == A_PHONE
    assert resolve(MOVED, arrived, [], None) == IN_THE_SANDBOX


def test_a_ring_handed_over_whose_leg_named_nobody_is_refused_rather_than_run_here() -> None:
    with pytest.raises(NotFound, match="names no agent"):
        resolve(Dispatch(env="sandbox", diverted_from="production"), A_PHONE, [AT_THE_NUMBER], None)


async def test_where_the_sandbox_has_its_own_livekit_the_ring_is_dialled_to_its_sip() -> None:
    server = Server()
    handed = RingHandoff(holder="m_ana", fleet="pinecall-sandbox", trunk=TO_THE_SANDBOX)
    assert await handed_on(server, "call_1", handed=handed, moved=MOVED, number=NUMBER)
    (leg,) = server.dialled.requests
    assert isinstance(leg, CreateSIPParticipantRequest)
    assert (leg.sip_call_to, leg.sip_number, leg.room_name) == (NUMBER, CALLER, "call_1")
    assert (leg.trunk.hostname, leg.trunk.auth_username) == ("sip.sandbox.box.test", USERNAME)
    assert (dict(leg.headers)[HEADER_OF[ORG]], dict(leg.headers)[HEADER_OF[HOLDER]]) == (
        "org_a",
        "m_ana",
    )
    assert leg.wait_until_answered
    assert server.dispatcher.made == []
    await server.aclose()


async def test_where_the_worlds_share_a_livekit_the_sandboxs_fleet_is_sent_into_the_room() -> None:
    server = Server()
    handed = RingHandoff(holder="m_ana", fleet="pinecall-sandbox")
    assert await handed_on(server, "call_1", handed=handed, moved=MOVED, number=NUMBER)
    (sent,) = server.dispatcher.made
    assert (sent.room, sent.agent_name, read_dispatch(sent.metadata)) == (
        "call_1",
        "pinecall-sandbox",
        MOVED,
    )
    assert server.dialled.requests == []
    await server.aclose()


async def test_a_hand_over_the_sandbox_does_not_answer_leaves_the_ring_here() -> None:
    server = Server()
    server.dialled.refusal = api.TwirpError("unavailable", "no answer", status=503)
    handed = RingHandoff(holder="m_ana", fleet="pinecall-sandbox", trunk=TO_THE_SANDBOX)
    assert not await handed_on(server, "call_1", handed=handed, moved=MOVED, number=NUMBER)
    await server.aclose()


def test_only_an_undispatched_ring_at_a_production_number_may_be_a_developers() -> None:
    assert may_be_a_developers(Dispatch(), A_PHONE, AT_THE_NUMBER)
    assert not may_be_a_developers(Dispatch(), A_PHONE, IN_THE_SANDBOX)
    assert not may_be_a_developers(Dispatch(agent="front"), A_PHONE, AT_THE_NUMBER)
    assert not may_be_a_developers(Dispatch(), A_PAGE, AT_THE_NUMBER)


def test_the_box_trunk_names_no_corner_and_a_token_names_its_own() -> None:
    assert named_by(Dispatch()) is None
    named = named_by(Dispatch(org="org_a", env="sandbox", holder="m_1"))
    assert named == Scope("org_a", "sandbox", "m_1")


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("486", "busy"),
        ("603", "busy"),
        ("480", "no_answer"),
        ("487", "no_answer"),
        ("500", "dial_failed"),
    ],
)
def test_the_carriers_answer_becomes_the_logs_own_word_for_it(code: str, reason: str) -> None:
    refused = api.TwirpError("unavailable", "sip", status=503, metadata={"sip_status_code": code})
    assert end_reason_of(refused) == reason


def test_anything_that_is_not_a_sip_answer_is_a_dial_that_failed() -> None:
    assert end_reason_of(GatewayRefused("GET /v1/agents/x/outbound-trunk: 409")) == "dial_failed"
    assert end_reason_of(api.TwirpError("internal", "no", status=500)) == "dial_failed"


def test_a_recorded_call_says_its_disclosure_then_the_notice_and_an_unrecorded_one_no_notice() -> (
    None
):
    opened = OpenCallResponse(
        seconds_left=None,
        minutes=None,
        disclosure="This is an automated assistant calling on behalf of Acme.",
        recording_notice="This call may be recorded.",
    )
    assert opening_of(opened, recorded=True) == (
        "This is an automated assistant calling on behalf of Acme. This call may be recorded."
    )
    assert opening_of(opened, recorded=False) == opened.disclosure
    inbound = OpenCallResponse(seconds_left=None, minutes=None, recording_notice="Grabada.")
    assert opening_of(inbound, recorded=False) is None


async def _kinds(knocking: Knocking, call: str) -> list[str]:
    return [entry.type for entry in await knocking.gateway.logs.store.whole(call)]


# The `call.ended` of a leg nobody answered, and of the overflow's call, go the same way.
@postgres
async def test_a_call_ended_outside_a_session_is_written_once_when_its_first_answer_is_lost(
    knocking: Knocking,
) -> None:
    losing = LosingTheFirstBatchAnswer()
    client = losing_client(knocking, losing)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    ended = CallEnded(reason="no_answer", ended_by="platform", ended_at=time.time(), duration_s=0)
    await ended_and_sealed(client, writer_of(client, context.call), ended, "no_answer")
    assert losing.lost == 1
    assert (await _kinds(knocking, context.call)).count("call.ended") == 1
    assert await knocking.gateway.logs.store.sealed(context.call)
    assert await knocking.gateway.logs.store.written(context.call) == 1
    await client.aclose()
    await losing.real.aclose()


async def _no_tool(use: ToolUse, _speech: str | None) -> ToolResult:
    return ToolResult(call_id=use.call_id, name=use.name, output="ok")


async def _no_lookup(
    _tool: PlatformTool, _arguments: JsonObject, _speech: str | None
) -> JsonObject:
    return {}


async def _no_seal(_usage: list[ModelUsage], _outcome: str) -> None:
    return None


@postgres
async def test_a_command_the_session_refused_is_written_once_by_the_calls_writer(
    knocking: Knocking, acme: str
) -> None:
    del acme
    losing = LosingTheFirstBatchAnswer()
    client = losing_client(knocking, losing)
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    platform = Platform(
        append_many=partial(client.append_many, context.call),
        tool=_no_tool,
        lookup=_no_lookup,
        seal=_no_seal,
    )
    call = Call(context, AgentConfig(slug=AGENT), platform)
    session = text_session(call, Running(ACME, "a-key", options={"replies": []}))
    call.writing.open()
    undeclared: JsonObject = {"name": "nobody_declared_it", "data": {}}
    await applied(
        session, Command(type="call.event", agent=AGENT, call=context.call, data=undeclared)
    )
    await call.writing.close(5)
    entries = await knocking.gateway.logs.store.whole(context.call)
    errors = [entry for entry in entries if entry.type == "error"]
    assert losing.lost == 1
    assert [entry.data["code"] for entry in errors] == ["refused"]
    await client.aclose()
    await losing.real.aclose()


# The agent leaving ends no SIP leg: the room going is what hangs the caller up.
@pytest.mark.parametrize(
    ("ended", "gone"),
    [
        (("agent_hung_up", "agent"), True),
        (("timeout", "platform"), True),
        (("supervisor_ended", "supervisor"), True),
        (("transferred", "agent"), False),
        (None, False),
    ],
)
async def test_a_call_the_session_hung_up_takes_its_room_with_it_but_a_transfer_leaves_it(
    *, ended: tuple[EndReason, EndedBy] | None, gone: bool
) -> None:
    server = Server()
    server.rooms.existing = {"call_a": True}
    await room_over(server, "call_a", ended)
    assert ("call_a" not in server.rooms.existing) == gone
    await room_over(server, "call_a", ended)
    await server.aclose()

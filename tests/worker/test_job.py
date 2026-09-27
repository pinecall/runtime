"""Tests for how a job finds its call: the arrival, the route, whose phone rang."""

import pytest
from livekit import rtc

from pinecall.channels.routes import Dispatch
from pinecall.domain.errors import NotFound
from pinecall.domain.types import THE_WIDGET, Corner, Route
from pinecall.session.room import CALLER_NUMBER, DIALLED_NUMBER
from pinecall.worker.job import Arrival, arrival_of, may_be_a_developers, named_by, resolve
from tests.fakes import Room, seat

NUMBER = "+15550100"
CALLER = "+15550199"
SIP = rtc.ParticipantKind.PARTICIPANT_KIND_SIP

A_PHONE = Arrival(caller=CALLER, channel="phone", direction="inbound", number=NUMBER)
A_PAGE = Arrival(caller="room_1", channel=THE_WIDGET, direction="inbound")
AT_THE_NUMBER = Route(org="org_a", agent="front", channel="phone", number=NUMBER)
IN_THE_SANDBOX = Route(org="org_a", agent="front", channel="phone", number=NUMBER, env="sandbox")


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


def test_only_an_undispatched_ring_at_a_production_number_may_be_a_developers() -> None:
    assert may_be_a_developers(Dispatch(), A_PHONE, AT_THE_NUMBER)
    assert not may_be_a_developers(Dispatch(), A_PHONE, IN_THE_SANDBOX)
    assert not may_be_a_developers(Dispatch(agent="front"), A_PHONE, AT_THE_NUMBER)
    assert not may_be_a_developers(Dispatch(), A_PAGE, AT_THE_NUMBER)


def test_the_box_trunk_names_no_corner_and_a_token_names_its_own() -> None:
    assert named_by(Dispatch()) is None
    named = named_by(Dispatch(org="org_a", env="sandbox", holder="m_1"))
    assert named == Corner("org_a", "sandbox", "m_1")

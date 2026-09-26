"""A job arrives and somebody answers it: the dispatch, then the SIP seat, then the default."""

from __future__ import annotations

import asyncio
import json

import pytest
from livekit.protocol import agent as jobs

from pinecall.types import Route
from pinecall.types.dispatch import Handover
from pinecall.worker import job_target
from pinecall_testkit.fake_room import FakeRoom, a_caller, a_connected_room, a_widget, as_a_room
from tests.worker.fakes import a_job

pytestmark = pytest.mark.unit

# One org, three doors: two agents on the phone and one widget, which is enough for every branch.
CLINICA_PHONE = Route(org="pinecall", agent="clinica-norte", channel="phone", number="+59891111")
CLINICA_WEB = Route(org="pinecall", agent="clinica-norte", channel="web", number=None)
TIENDA_PHONE = Route(org="pinecall", agent="tienda-sur", channel="phone", number="+59892222")
ROUTES = (CLINICA_PHONE, CLINICA_WEB, TIENDA_PHONE)

# Every arrival below is read under this bound, and the wait for a SIP leg is five seconds: a job
# that waited for a seat it had no business waiting for fails here instead of slowing the suite.
NOTHING_MAY_WAIT_S = 0.05


async def test_a_dispatched_job_resolves_to_the_agent_its_metadata_names() -> None:
    arrival = await _arrival(a_job(metadata={"agent": "tienda-sur"}), _a_seat())
    assert job_target.resolve(arrival, ROUTES) == TIENDA_PHONE


# The spoken half of the seam that names a synthetic caller: the gateway holds the whole simulated
# call, but this process is what writes call.started, so the name arrives in the dispatch or not
# at all. Every other call names none, and reads as nobody playing anybody.
async def test_a_dispatched_simulation_says_which_synthetic_caller_is_being_played() -> None:
    arrival = await _arrival(
        a_job(metadata={"agent": "tienda-sur", "persona": "homeowner"}), _a_seat()
    )
    assert arrival.persona == "homeowner"


async def test_a_job_that_names_no_persona_is_a_call_nobody_is_playing() -> None:
    arrival = await _arrival(a_job(metadata={"agent": "tienda-sur"}), _a_seat())
    assert arrival.persona is None


async def test_a_dispatched_simulation_carries_the_callers_own_rule_for_the_call() -> None:
    said = {"agent": "tienda-sur", "persona": "homeowner", "accepts_when": "a price"}
    arrival = await _arrival(a_job(metadata=said), _a_seat())
    assert (arrival.accepts_when, arrival.declines_when) == ("a price", None)


# THE bug: livekit fills `job.participant` for a publisher job and leaves it empty for a room job,
# which is what a SIP dispatch rule creates — so a real INVITE only routes if the number is read
# off the seat in the room. Every job in this file has an empty participant, this one included.
async def test_an_empty_job_resolves_by_the_number_dialled_on_the_seat_in_the_room() -> None:
    job = a_job()
    assert not job.participant.attributes
    arrival = await _arrival(job, _a_seat(dialled="+59891111"))
    assert job_target.resolve(arrival, ROUTES) == CLINICA_PHONE


async def test_the_number_that_was_dialled_is_the_door_and_the_caller_is_the_other_number() -> None:
    arrival = await _arrival(a_job(), _a_seat(dialled="+59891111", caller="+59899999"))
    assert (arrival.number, arrival.caller, arrival.channel) == ("+59891111", "+59899999", "phone")


async def test_a_job_that_dialled_nothing_arrived_through_the_widget() -> None:
    arrival = await _arrival(a_job(room="call_web_1"), a_connected_room(a_widget()))
    assert (arrival.channel, arrival.number, arrival.caller) == ("web", None, "call_web_1")


async def test_the_dispatch_is_read_before_the_number_when_a_job_carries_both() -> None:
    arrival = await _arrival(a_job(metadata={"agent": "tienda-sur"}), _a_seat(dialled="+59891111"))
    assert job_target.resolve(arrival, ROUTES).agent == "tienda-sur"


async def test_a_job_that_names_nobody_falls_to_the_agent_the_process_was_started_with() -> None:
    arrival = await _arrival(a_job(), a_connected_room(a_widget()))
    assert job_target.resolve(arrival, ROUTES, default="clinica-norte") == CLINICA_WEB


# The attribute names are spelled out here on purpose: livekit stamps them on the SIP leg and the
# whole phone routing hangs off those exact strings, so a rename in sip.py has to fail here.
async def test_a_phone_job_resolves_the_tenant_from_the_trunk_number_livekit_stamped() -> None:
    seat = a_caller("+59899999")
    seat.attributes = {"sip.trunkPhoneNumber": "+59892222", "sip.phoneNumber": "+59899999"}
    arrival = await _arrival(a_job(), a_connected_room(seat))
    route = job_target.resolve(arrival, ROUTES)
    assert (route.org, route.agent, route.channel) == ("pinecall", "tienda-sur", "phone")
    assert arrival.caller == "+59899999"


async def test_a_job_that_names_nobody_and_has_no_default_is_refused_by_name() -> None:
    arrival = await _arrival(a_job(), a_connected_room(a_widget()))
    with pytest.raises(job_target.NoRoute):
        job_target.resolve(arrival, ROUTES)


async def test_an_agent_answers_only_on_the_channel_the_call_arrived_through() -> None:
    """A dispatch that names no corner cannot be a widget call of ours: the gateway writes those."""
    named = a_job(metadata={"agent": "tienda-sur"})
    dialled = await _arrival(named, _a_seat())
    written = await _arrival(named, a_connected_room(a_widget()))
    assert job_target.resolve(dialled, ROUTES) == TIENDA_PHONE
    with pytest.raises(job_target.NoRoute):
        job_target.resolve(written, ROUTES)


# A number is a row somebody bought and the widget is not: there is no web door in any table, and
# every agent is on the web. So a browser's call carries its own route — this agent, in the corner
# the dispatch named — and an agent with a telephone and no widget answers a page all the same.
async def test_a_browsers_call_needs_no_door_and_runs_in_the_corner_the_dispatch_named() -> None:
    named = a_job(
        metadata={"agent": "tienda-sur", "org": "tienda", "env": "sandbox", "holder": "m_1"}
    )
    arrival = await _arrival(named, a_connected_room(a_widget()))

    route = job_target.resolve(arrival, ROUTES)

    assert (route.org, route.env, route.agent, route.channel) == (
        "tienda",
        "sandbox",
        "tienda-sur",
        "web",
    )
    assert route.number is None


async def test_a_row_for_the_web_still_wins_over_the_one_a_dispatch_would_make() -> None:
    """What a table says is what answers, here as everywhere: the made-up route is the last word."""
    named = a_job(metadata={"agent": "tienda-sur", "org": "somebody-else", "env": "production"})
    arrival = await _arrival(named, a_connected_room(a_widget()))
    web = Route(org="tienda", agent="tienda-sur", channel="web", env="sandbox")

    assert job_target.resolve(arrival, [*ROUTES, web]) == web


async def test_a_number_nobody_answers_is_refused_and_the_message_names_it() -> None:
    arrival = await _arrival(a_job(), _a_seat(dialled="+59893333"))
    with pytest.raises(job_target.NoRoute, match=r"\+59893333"):
        job_target.resolve(arrival, ROUTES)


async def test_a_dial_says_outbound_in_its_metadata_and_everything_else_is_inbound() -> None:
    out = a_job(metadata={"agent": "tienda-sur", "direction": "outbound"})
    assert (await _arrival(out, _a_seat())).direction == "outbound"
    dialled = a_job(metadata={"agent": "tienda-sur"})
    assert (await _arrival(dialled, _a_seat())).direction == "inbound"


# The sharpest thing about an outbound arrival: there is no SIP seat to read, because this job is
# what will create one. Reading the room would make every call we place a web call, and then the
# agent would be refused for having no widget.
async def test_a_dialled_call_is_a_phone_call_before_anybody_is_on_the_line() -> None:
    placed = a_job(metadata={"agent": "tienda-sur", "direction": "outbound"})
    arrival = await _arrival(placed, a_connected_room())
    assert (arrival.channel, arrival.direction, arrival.number) == ("phone", "outbound", None)
    assert job_target.resolve(arrival, ROUTES).channel == "phone"


# The far end is the contact on a call we placed: it is who the call is with, and what memory
# files it under. Without it the caller would be the room's name, which is the call id.
async def test_the_caller_of_a_dialled_call_is_the_number_the_dispatch_named() -> None:
    placed = a_job(
        metadata={"agent": "tienda-sur", "direction": "outbound", "caller": "+59899111111"}
    )
    assert (await _arrival(placed, a_connected_room())).caller == "+59899111111"


def test_a_dispatch_says_whose_the_call_is_before_the_room_is_joined() -> None:
    """The org, the world and the corner ride the metadata; a dispatch that says nothing is
    the box's own trunk, and a world it spelled wrong reads as none rather than as a refusal."""
    named = a_job(
        metadata={"agent": "tienda-sur", "org": "tienda", "env": "sandbox", "holder": "m_1"}
    )
    assert job_target.whose(named) == job_target.Whose(org="tienda", env="sandbox", holder="m_1")
    assert job_target.whose(a_job(metadata={"agent": "tienda-sur"})) == job_target.Whose()
    assert job_target.whose(a_job(metadata={"org": "tienda", "env": "staging"})).env is None


async def test_an_arrival_carries_whose_it_is() -> None:
    arrival = await _arrival(a_job(metadata={"agent": "tienda-sur", "org": "tienda"}), _a_seat())
    assert arrival.whose.org == "tienda"


async def test_metadata_that_is_not_a_dispatch_is_not_an_error() -> None:
    """A room somebody created by hand carries whatever they typed; the call still resolves."""
    for said in ("not json at all", "[1, 2, 3]", ""):
        arrival = await _arrival(a_job(metadata=said), _a_seat())
        assert arrival.agent is None and arrival.metadata == {}


def _a_seat(dialled: str = "+59892222", caller: str = "+59897777") -> FakeRoom:
    """A room with the caller's leg already on it, the way an inbound call opens one."""
    return a_connected_room(a_caller(caller, dialled=dialled))


async def _arrival(job: jobs.Job, room: FakeRoom) -> job_target.Arrival:
    """This job's arrival in this room, read the way `entry.answer` reads it, and never waiting."""
    async with asyncio.timeout(NOTHING_MAY_WAIT_S):
        return await job_target.arrival_of(job, as_a_room(room))


def a_ring(**changed: object) -> job_target.Arrival:
    """A phone call dialled to the clinic's number, as the SIP seat says it."""
    said: dict[str, object] = {
        "caller": "+59897777",
        "channel": "phone",
        "direction": "inbound",
        "number": "+59891111",
    }
    said.update(changed)
    return job_target.Arrival(**said)  # type: ignore[arg-type]


def test_a_ring_to_a_production_number_is_asked_about_and_nothing_else_is() -> None:
    """Only a production phone call nobody aimed can be a developer's own: a dispatch, a widget
    visit, an outbound call and a sandbox number are already where they were sent."""
    assert job_target.may_be_a_developers(a_ring(), CLINICA_PHONE)
    assert not job_target.may_be_a_developers(a_ring(agent="clinica-norte"), CLINICA_PHONE)
    assert not job_target.may_be_a_developers(a_ring(direction="outbound"), CLINICA_PHONE)
    assert not job_target.may_be_a_developers(a_ring(number=None, channel="web"), CLINICA_WEB)
    sandbox = Route(
        org="pinecall", agent="clinica-norte", channel="phone", number="+59891111", env="sandbox"
    )
    assert not job_target.may_be_a_developers(a_ring(), sandbox)


# The room is handed over and not rebuilt: a dispatch into the SAME room, to the fleet the answer
# named — production never knows what the sandbox calls its workers — saying whose call it is the
# way every dispatch of ours does, and where it rang.
def test_a_developers_ring_is_dispatched_into_the_same_room_to_the_fleet_the_answer_named() -> None:
    handover = Handover(holder="m_berna", fleet="pinecall-sandbox")

    dispatch = job_target.handing_over("call-+59897777_abc", a_ring(), CLINICA_PHONE, handover)

    assert (dispatch.room, dispatch.agent_name) == ("call-+59897777_abc", "pinecall-sandbox")
    assert json.loads(dispatch.metadata) == {
        "org": "pinecall",
        "env": "sandbox",
        "holder": "m_berna",
        "agent": "clinica-norte",
        "caller": "+59897777",
        "direction": "inbound",
        "diverted_from": "production",
    }


# The other fleet's router reads that dispatch like any other, and the call is built in the
# developer's corner on the number it RANG — which no table of that instance holds, and which a
# sandbox number of the same agent must not stand in for.
async def test_the_fleet_it_is_handed_to_builds_it_on_the_number_it_rang_in_the_corner_named() -> (
    None
):
    handover = Handover(holder="m_berna", fleet="pinecall-sandbox")
    dispatch = job_target.handing_over("call_1", a_ring(), CLINICA_PHONE, handover)
    its_own_number = Route(
        org="pinecall", agent="clinica-norte", channel="phone", number="+59829001199", env="sandbox"
    )

    arrival = await _arrival(a_job(metadata=dispatch.metadata), _a_seat(dialled="+59891111"))
    route = job_target.resolve(arrival, (its_own_number,))

    assert route == Route(
        org="pinecall", agent="clinica-norte", channel="phone", number="+59891111", env="sandbox"
    )
    assert arrival.whose == job_target.Whose(org="pinecall", env="sandbox", holder="m_berna")
    assert arrival.metadata["diverted_from"] == "production"
    assert not job_target.may_be_a_developers(arrival, route)

"""The caller's leg, found in the room: already seated, a moment late, never, and not a phone."""

from __future__ import annotations

import asyncio

import pytest

from pinecall.session.voice import sip
from tests.session.voice.room.fakes import FakeRoom, a_caller, a_connected_room, a_widget, as_a_room

pytestmark = pytest.mark.unit

CALLER = "+59897777"

# Short enough that nothing sits on it, long enough that a leg one loop late still lands.
A_MOMENT = 0.05

# Long enough that a test which waited at all would hang instead of passing.
NEVER = 60.0


async def test_the_leg_already_seated_when_the_agent_arrives_is_found_without_waiting() -> None:
    room = a_connected_room(a_caller(CALLER))
    leg = await sip.the_sip_leg(as_a_room(room), "phone", wait=0)
    assert leg is not None
    assert leg.identity == f"sip_{CALLER}"


async def test_a_leg_that_joins_a_moment_after_the_agent_is_waited_for() -> None:
    room = a_connected_room()

    async def joins_late() -> None:
        await asyncio.sleep(0)
        room.join(a_caller(CALLER))

    leg, _ = await asyncio.gather(sip.the_sip_leg(as_a_room(room), "phone"), joins_late())
    assert leg is not None
    assert leg.identity == f"sip_{CALLER}"


async def test_a_phone_that_never_joins_gives_up_at_the_bound_and_answers_nobody() -> None:
    room = a_connected_room(a_widget())
    assert await sip.the_sip_leg(as_a_room(room), "phone", wait=A_MOMENT) is None


async def test_a_console_session_is_skipped_and_never_waits_for_a_leg_nobody_dialled() -> None:
    room = a_connected_room(a_widget())
    assert await sip.the_sip_leg(as_a_room(room), "web", wait=NEVER) is None


async def test_a_room_that_never_connected_has_no_leg_either() -> None:
    room = FakeRoom()
    assert await sip.the_sip_leg(as_a_room(room), "phone", wait=NEVER) is None


# The router asks before it knows the channel — that is the question it is asking — so the room
# answers for it: somebody already seated who is not a phone means nobody dialled.
async def test_a_room_a_browser_is_already_sitting_in_is_answered_without_a_channel_and_at_once():
    room = a_connected_room(a_widget())
    assert await sip.the_sip_leg(as_a_room(room), wait=NEVER) is None


async def test_an_empty_room_is_waited_in_when_nobody_has_said_which_door_this_is() -> None:
    room = a_connected_room()

    async def joins_late() -> None:
        await asyncio.sleep(0)
        room.join(a_caller(CALLER))

    leg, _ = await asyncio.gather(sip.the_sip_leg(as_a_room(room)), joins_late())
    assert leg is not None and leg.identity == f"sip_{CALLER}"


async def test_a_dispatch_that_named_its_agent_reads_the_seat_it_has_and_waits_for_none() -> None:
    empty = a_connected_room()
    seated = a_connected_room(a_caller(CALLER))
    assert await sip.the_sip_leg(as_a_room(empty), wait=0) is None
    assert await sip.the_sip_leg(as_a_room(seated), wait=0) is not None


def test_the_two_numbers_of_a_call_are_read_off_the_seat_by_livekits_own_attribute_names() -> None:
    numbers = sip.the_numbers(a_caller(CALLER, dialled="+59891111").attributes)
    assert (numbers.caller, numbers.dialled) == (CALLER, "+59891111")


def test_a_seat_that_is_not_a_phone_has_neither_number() -> None:
    assert sip.the_numbers(a_widget().attributes) == sip.Numbers()

"""Whose voice the session answers when the room holds a listener and a supervisor as well."""

from __future__ import annotations

import asyncio

import pytest

from pinecall.tokens.scopes import SCOPE_ATTRIBUTE
from pinecall.types import Scope
from pinecall.worker import caller_seat
from tests.session.voice.room.fakes import (
    FakeParticipant,
    a_caller,
    a_connected_room,
    a_widget,
    as_a_room,
)

pytestmark = pytest.mark.unit

THE_CALLER = "+59897777"

# What the waiting tests give a seat that is never coming. Real enough to be waited on, short
# enough that a suite does not spend WAIT_FOR_THE_CALLER_S proving a negative.
A_MOMENT_S = 0.05


async def test_a_phone_call_is_heard_on_the_leg_that_dialled_in() -> None:
    """The desk and the ear were seated first; the seat is still the phone's."""
    room = a_connected_room(_listening("ear"), _supervising("ana"), a_caller(THE_CALLER))
    assert await caller_seat.wait_for_caller_seat(as_a_room(room), "phone") == f"sip_{THE_CALLER}"


async def test_a_web_call_is_heard_on_the_browser_holding_the_talk_token() -> None:
    """A talk token is the one scope minted for the person the agent serves."""
    room = a_connected_room(_listening("ear"), _supervising("ana"), a_widget("web_ab12cd34ef56"))
    assert await caller_seat.wait_for_caller_seat(as_a_room(room), "web") == "web_ab12cd34ef56"


async def test_a_written_visit_waits_for_no_seat_at_all() -> None:
    """A chat token publishes no voice: waiting for its talk seat could only time out, and did —
    five seconds of silence before every greeting on the web chat (2026-09-16)."""
    room = a_connected_room(_listening("ear"))
    began = asyncio.get_running_loop().time()
    assert await caller_seat.wait_for_caller_seat(as_a_room(room), "web", spoken=False) is None
    assert asyncio.get_running_loop().time() - began < A_MOMENT_S


async def test_a_supervisor_who_speaks_into_the_room_is_never_the_seat() -> None:
    """The whole point: ms-8 puts a human on a microphone in this room, and the agent ignores it."""
    room = a_connected_room(_supervising("ana"))
    assert await caller_seat.wait_for_caller_seat(as_a_room(room), "web", wait=A_MOMENT_S) is None


async def test_a_listener_is_never_the_seat_either() -> None:
    """An observe token is hidden and silent, and it is not who the call is with."""
    room = a_connected_room(_listening("ear"))
    assert await caller_seat.wait_for_caller_seat(as_a_room(room), "web", wait=A_MOMENT_S) is None


async def test_a_caller_who_is_still_connecting_is_waited_for() -> None:
    """The bug this wait exists for: a dispatch that names its agent is waited for nowhere, so on
    a `--voice` run the worker reaches the seat before the caller has joined. Unpinned, the agent
    starts with no input audio and livekit then builds no recorder at all — the call is held and
    nothing is recorded."""
    room = a_connected_room(_listening("ear"))
    late = a_widget("web_still_dialling")

    async def joins_a_moment_later() -> None:
        await asyncio.sleep(0)
        room.join(late)

    seated, _ = await asyncio.gather(
        caller_seat.wait_for_caller_seat(as_a_room(room), "web"), joins_a_moment_later()
    )
    assert seated == "web_still_dialling"


async def test_a_caller_who_never_arrives_does_not_hold_the_call_forever() -> None:
    """Bounded, like the leg's own wait: the session starts on livekit's first-comer rule."""
    room = a_connected_room(_listening("ear"))
    assert await caller_seat.wait_for_caller_seat(as_a_room(room), "web", wait=A_MOMENT_S) is None


async def test_a_seat_already_taken_is_answered_without_waiting() -> None:
    """The common case pays nothing: a caller already in the room is read, not waited for."""
    room = a_connected_room(a_widget("web_here_already"))
    async with asyncio.timeout(A_MOMENT_S):
        assert await caller_seat.wait_for_caller_seat(as_a_room(room), "web") == "web_here_already"


async def test_a_room_that_never_connected_has_nobody_coming() -> None:
    """The same answer the leg's wait gives: a room with no connection has no seat to wait for."""
    room = a_connected_room()
    room.connected = False
    assert await caller_seat.wait_for_caller_seat(as_a_room(room), "web") is None


async def test_a_written_channel_has_no_seat_to_listen_to() -> None:
    """WhatsApp is a room nobody joins: every turn arrives as text, and no audio is subscribed."""
    room = a_connected_room(_supervising("ana"))
    async with asyncio.timeout(A_MOMENT_S):
        assert await caller_seat.wait_for_caller_seat(as_a_room(room), "whatsapp") is None


def _listening(identity: str) -> FakeParticipant:
    """A seat that came in on an observe token: the Calls screen, listening in."""
    return _seated(identity, "observe")


def _supervising(identity: str) -> FakeParticipant:
    """A seat that came in on a supervise token: the desk, which from ms-8 also speaks."""
    return _seated(identity, "supervise")


def _seated(identity: str, scope: Scope) -> FakeParticipant:
    """A participant livekit seated with a token, its scope on the attribute the token wrote."""
    return FakeParticipant(identity=identity, attributes={SCOPE_ATTRIBUTE: scope})

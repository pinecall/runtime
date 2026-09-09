"""Whose voice the session answers when the room holds a listener and a supervisor as well."""

from __future__ import annotations

import pytest

from pinecall.auth.scopes import SCOPE_ATTRIBUTE
from pinecall.types import Scope
from pinecall.worker import seat
from tests.session.voice.room.fakes import (
    FakeParticipant,
    a_caller,
    a_connected_room,
    a_widget,
    as_a_room,
)

pytestmark = pytest.mark.unit

THE_CALLER = "+59897777"


async def test_a_phone_call_is_heard_on_the_leg_that_dialled_in() -> None:
    """The desk and the ear were seated first; the seat is still the phone's."""
    room = a_connected_room(_listening("ear"), _supervising("ana"), a_caller(THE_CALLER))
    assert await seat.the_callers_seat(as_a_room(room), "phone") == f"sip_{THE_CALLER}"


async def test_a_web_call_is_heard_on_the_browser_holding_the_talk_token() -> None:
    """A talk token is the one scope minted for the person the agent serves."""
    room = a_connected_room(_listening("ear"), _supervising("ana"), a_widget("web_ab12cd34ef56"))
    assert await seat.the_callers_seat(as_a_room(room), "web") == "web_ab12cd34ef56"


async def test_a_supervisor_who_speaks_into_the_room_is_never_the_seat() -> None:
    """The whole point: ms-8 puts a human on a microphone in this room, and the agent ignores it."""
    room = a_connected_room(_supervising("ana"))
    assert await seat.the_callers_seat(as_a_room(room), "web") is None


async def test_a_listener_is_never_the_seat_either() -> None:
    """An observe token is hidden and silent, and it is not who the call is with."""
    room = a_connected_room(_listening("ear"))
    assert await seat.the_callers_seat(as_a_room(room), "web") is None


async def test_a_room_the_agent_reached_first_pins_nobody() -> None:
    """Nobody to point at yet: the identity stays unset and livekit's own first-comer rule runs."""
    assert await seat.the_callers_seat(as_a_room(a_connected_room()), "web") is None


async def test_a_written_channel_has_no_seat_to_listen_to() -> None:
    """WhatsApp is a room nobody joins: every turn arrives as text, and no audio is subscribed."""
    room = a_connected_room(_supervising("ana"))
    assert await seat.the_callers_seat(as_a_room(room), "whatsapp") is None


def _listening(identity: str) -> FakeParticipant:
    """A seat that came in on an observe token: the Calls screen, listening in."""
    return _seated(identity, "observe")


def _supervising(identity: str) -> FakeParticipant:
    """A seat that came in on a supervise token: the desk, which from ms-8 also speaks."""
    return _seated(identity, "supervise")


def _seated(identity: str, scope: Scope) -> FakeParticipant:
    """A participant livekit seated with a token, its scope on the attribute the token wrote."""
    return FakeParticipant(identity=identity, attributes={SCOPE_ATTRIBUTE: scope})

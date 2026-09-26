"""call.dtmf: the tones that reach the caller's leg, the pause between them, and the refusals."""

from __future__ import annotations

import pytest

from pinecall.session.voice.room import dtmf
from tests.session.voice.room.fakes import Held, a_caller, a_held_room
from tests.session.voice.room.test_verbs import applied

pytestmark = pytest.mark.unit

CALLER = "+59897777"


@pytest.fixture
async def held() -> Held:
    room = a_held_room()
    room.room.connect()
    room.room.join(a_caller(CALLER))
    return room


async def test_every_tone_goes_down_the_line_in_the_order_it_was_given(held: Held) -> None:
    await applied(held, "call.dtmf", {"digits": "12#"})
    assert held.room.local_participant.tones == [(1, "1"), (2, "2"), (11, "#")]
    assert held.recording.of("error") == []


async def test_a_comma_is_a_pause_and_not_a_tone(held: Held) -> None:
    await applied(held, "call.dtmf", {"digits": "1,2"})
    assert [digit for _, digit in held.room.local_participant.tones] == ["1", "2"]


async def test_something_that_is_not_a_touch_tone_sends_nothing_at_all(held: Held) -> None:
    await applied(held, "call.dtmf", {"digits": "12A"})
    assert held.room.local_participant.tones == []
    (error,) = held.recording.of("error")
    assert error.data["command"] == dtmf.VERB
    assert "'A'" in error.data["message"]


async def test_a_call_with_no_phone_leg_has_nothing_to_send_tones_down() -> None:
    web = a_held_room(channel="web")
    web.room.connect()
    await applied(web, "call.dtmf", {"digits": "1"})
    assert web.room.local_participant.tones == []
    (error,) = web.recording.of("error")
    assert error.data["command"] == dtmf.VERB

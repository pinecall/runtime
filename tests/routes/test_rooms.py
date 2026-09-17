"""Which rooms the SFU still has: the one question the reaper asks it, and how it is asked."""

import pytest

from pinecall._settings import Settings
from pinecall.routes.rooms import AT_MOST, LivekitRooms, MemoryRooms, in_batches, rooms_for

pytestmark = pytest.mark.unit


async def test_a_room_that_is_there_answers_and_one_that_is_not_is_simply_absent() -> None:
    """The absence IS the answer: livekit deletes an empty room, so a gone room is a gone call."""
    rooms = MemoryRooms(["call_live"])
    assert await rooms.still_open(["call_live", "call_gone"]) == {"call_live"}
    assert await rooms.still_open([]) == set()


def test_the_names_are_asked_for_in_batches_so_a_hundred_calls_are_one_round_trip() -> None:
    """They ride in one request's query: a box with more open calls than that asks twice."""
    names = [f"call_{n}" for n in range(AT_MOST + 3)]
    batches = list(in_batches(names, AT_MOST))
    assert [len(batch) for batch in batches] == [AT_MOST, 3]
    assert [name for batch in batches for name in batch] == names, "in the order given, once each"
    assert list(in_batches([], AT_MOST)) == []


def test_the_real_sfu_needs_the_livekit_pair_and_is_none_without_it() -> None:
    """A gateway that cannot ask which calls are running reaps nothing (api/app.py)."""
    assert rooms_for(Settings(livekit_api_key=None, livekit_api_secret=None)) is None
    assert isinstance(
        rooms_for(Settings(livekit_api_key="k", livekit_api_secret="s" * 32)), LivekitRooms
    )

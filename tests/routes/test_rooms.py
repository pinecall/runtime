"""Which rooms an agent is still in: the one question the reaper asks the SFU, and how."""

import pytest

from pinecall._settings import Settings
from pinecall.routes.rooms import AT_MOST, LivekitRooms, MemoryRooms, in_batches, rooms_for

pytestmark = pytest.mark.unit


async def test_only_a_room_an_agent_is_in_answers() -> None:
    """A room with only people left in it is not a call anybody is running."""
    rooms = MemoryRooms(["call_live"], agentless=["call_people_only"])
    assert await rooms.with_an_agent(["call_live", "call_people_only", "call_gone"]) == {
        "call_live"
    }
    assert await rooms.with_an_agent([]) == set()


async def test_a_room_closed_is_gone_whoever_was_in_it() -> None:
    rooms = MemoryRooms(["call_live"])
    await rooms.closed("call_live")
    await rooms.closed("call_gone")
    assert await rooms.with_an_agent(["call_live"]) == set()
    assert rooms.taken_down == ["call_live", "call_gone"]


def test_the_names_are_asked_for_in_batches_so_a_hundred_calls_are_one_round_trip() -> None:
    """They ride in one request's query: a box with more open calls than that asks twice."""
    names = [f"call_{n}" for n in range(AT_MOST + 3)]
    batches = list(in_batches(names, AT_MOST))
    assert [len(batch) for batch in batches] == [AT_MOST, 3]
    assert [name for batch in batches for name in batch] == names, "in the order given, once each"
    assert list(in_batches([], AT_MOST)) == []


def test_the_real_sfu_needs_the_livekit_pair_and_is_none_without_it() -> None:
    """A gateway that cannot ask which calls are running reaps nothing (api/app.py)."""
    assert (
        rooms_for(Settings(world="production", livekit_api_key=None, livekit_api_secret=None))
        is None
    )
    assert isinstance(
        rooms_for(Settings(world="production", livekit_api_key="k", livekit_api_secret="s" * 32)),
        LivekitRooms,
    )

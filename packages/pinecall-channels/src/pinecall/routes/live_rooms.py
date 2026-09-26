"""Which rooms an agent is still in: the one fact that says a call in a room is still running."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Protocol

from livekit import api

from pinecall.routes.sfu import Sfu
from pinecall.settings import Settings

# livekit's own list takes the names to ask about, so a hundred unsealed calls are one round trip
# and never a hundred. Asked in batches because the names ride in the query of one request.
AT_MOST = 100

# The participant kind a worker's job joins a room as (livekit's ParticipantInfo.Kind.AGENT).
AGENT = api.ParticipantInfo.Kind.AGENT


class Rooms(Protocol):
    """What the reaper asks the media plane: which calls an agent is still in, and closing one."""

    async def with_an_agent(self, names: Collection[str]) -> set[str]:
        """The subset of these rooms the SFU has with an agent in them. Empty is: none of them."""
        ...

    async def closed(self, name: str) -> None:
        """Take this room down, and whoever is still in it out. A room that is gone is nothing."""
        ...


# The room is the call, and the AGENT in it is what runs it: a worker's job joins as livekit's agent
# kind. A room is not enough — people stay in one after the job is gone: the caller's tab left
# open, a supervisor's seat — and a room with only people in it is a call nobody is running
# (call_0c4dd599…, 2026-09-24: the job was killed, two tabs kept the room up for hours, and the
# console showed the call live with nothing behind it).
class LivekitRooms:
    """The real SFU, over livekit-api: which of these rooms exist with an agent still in them."""

    def __init__(self, sfu: Sfu) -> None:
        self._sfu = sfu

    async def with_an_agent(self, names: Collection[str]) -> set[str]:
        """Every one of these rooms the SFU has and an agent is in. The rest are nobody's."""
        wanted = list(names)
        if not wanted:
            return set()
        served: set[str] = set()
        async with self._sfu.api() as livekit:
            for batch in in_batches(wanted, AT_MOST):
                rooms = await livekit.room.list_rooms(api.ListRoomsRequest(names=batch))
                for room in rooms.rooms:
                    asked = api.ListParticipantsRequest(room=room.name)
                    people = await livekit.room.list_participants(asked)
                    if any(one.kind == AGENT for one in people.participants):
                        served.add(room.name)
        return served

    async def closed(self, name: str) -> None:
        """Delete the room; livekit disconnects everybody still in it."""
        async with self._sfu.api() as livekit:
            try:
                await livekit.room.delete_room(api.DeleteRoomRequest(room=name))
            except api.TwirpError:
                return


def in_batches(names: list[str], size: int) -> Iterable[list[str]]:
    """The names in slices of at most `size`, in the order they were given."""
    for start in range(0, len(names), size):
        yield names[start : start + size]


def rooms_for(settings: Settings) -> Rooms | None:
    """The SFU when the process has the LiveKit pair; None when it has none, and nothing reaps."""
    sfu = Sfu.of(settings)
    return None if sfu is None else LivekitRooms(sfu)

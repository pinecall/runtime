"""Which of these rooms the SFU still has: the one fact that says a spoken call is still running."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Protocol

from livekit import api

from pinecall._settings import Settings

# livekit's own list takes the names to ask about, so a hundred unsealed calls are one round trip
# and never a hundred. Asked in batches because the names ride in the query of one request.
AT_MOST = 100


class Rooms(Protocol):
    """What the reaper asks the media plane: of these rooms, which ones are still there."""

    async def still_open(self, names: Collection[str]) -> set[str]:
        """The subset of these room names the SFU has right now. Empty is: none of them."""
        ...


class MemoryRooms:
    """The media plane of a clone with no LiveKit pair, and of every test: what would be there."""

    def __init__(self, open: Iterable[str] = ()) -> None:
        self.open = set(open)

    async def still_open(self, names: Collection[str]) -> set[str]:
        return {name for name in names if name in self.open}


# A room the agent left empties, and livekit deletes an empty room by itself (infra/box/livekit.yaml
# `empty_timeout: 60`). So "the SFU has no such room" is not a guess about a worker: it is the media
# plane saying nobody is on this call and nobody has been for a minute.
class LivekitRooms:
    """The real SFU, over livekit-api: the rooms of these names that exist, in batches."""

    def __init__(self, url: str, api_key: str, api_secret: str) -> None:
        self._url = url
        self._key = api_key
        self._secret = api_secret

    async def still_open(self, names: Collection[str]) -> set[str]:
        """Every one of these names the SFU answers with. A name it does not know is not in it."""
        wanted = list(names)
        if not wanted:
            return set()
        standing: set[str] = set()
        async with api.LiveKitAPI(self._url, self._key, self._secret) as livekit:
            for batch in in_batches(wanted, AT_MOST):
                rooms = await livekit.room.list_rooms(api.ListRoomsRequest(names=batch))
                standing.update(room.name for room in rooms.rooms)
        return standing


def in_batches(names: list[str], size: int) -> Iterable[list[str]]:
    """The names in slices of at most `size`, in the order they were given."""
    for start in range(0, len(names), size):
        yield names[start : start + size]


def rooms_for(settings: Settings) -> Rooms | None:
    """The SFU when the process has the LiveKit pair; None when it has none, and nothing reaps."""
    if settings.livekit_api_key and settings.livekit_api_secret:
        return LivekitRooms(
            settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
        )
    return None

"""The other instance, asked on a peer key: whose a production ring is, and production's numbers."""

from __future__ import annotations

import httpx
from pydantic import TypeAdapter

from pinecall._exceptions import PinecallError
from pinecall.types import PRODUCTION, Route
from pinecall.types.dispatch import Handover
from pinecall_protocol import WireModel

# The two doors one instance knocks at on the other's: the developer's-phone question, which
# production asks its sandbox (api/agents/endpoints.py), and the numbers the customers call, which
# the sandbox asks production. Both are the FLEET's doors — a fleet key names the org it asks for
# — so the key each instance holds of the other is a fleet key minted there (`box peer`).
RINGS_FOR = "/v1/agents/{slug}/rings-for"
ROUTES = "/v1/routes"

# A phone is ringing while the first is asked, and a person is looking at a screen while the
# second is: two seconds unanswered is the other instance not there, and the asker goes on
# without it — the ring stays production's, the numbers are refused in one sentence.
TIMEOUT_S = 2.0

UNREACHABLE = "{url} did not answer the peer key: {said}"

ROUTES_OF: TypeAdapter[tuple[Route, ...]] = TypeAdapter(tuple[Route, ...])


# The door's answer, on both sides of it: the worker reads it off its own gateway, and production
# reads it off its sandbox's. Both fields or neither: a ring is somebody's corner in some fleet.
class RingsFor(WireModel):
    """`GET /v1/agents/{slug}/rings-for`: the corner a production ring goes to, or nobody's."""

    holder: str | None = None
    fleet: str | None = None

    @classmethod
    def of(cls, handover: Handover | None) -> RingsFor:
        """The answer for a hand-over, or production's own when there is none."""
        if handover is None:
            return cls()
        return cls(holder=handover.holder, fleet=handover.fleet)

    def handover(self) -> Handover | None:
        """Whose corner and which fleet, or None: the call stays where it rang."""
        if not self.holder or not self.fleet:
            return None
        return Handover(holder=self.holder, fleet=self.fleet)


class PeerUnreachable(PinecallError):
    """The other instance did not answer, refused the key, or answered what is not its door's."""


# Built like the identity (auth/identity.py): the process's one httpx client, the other's public
# URL, and — unlike the identity, which is asked with a code a person carried — a key of its own.
class Peer:
    """The other instance's gateway, asked on the fleet key it minted for this one."""

    def __init__(self, http: httpx.AsyncClient, url: str, key: str) -> None:
        self._http = http
        self._url = url.rstrip("/")
        self._key = key

    async def rings_for(self, slug: str, *, org: str, caller: str) -> Handover | None:
        """The developer whose copy takes this ring there, or None. PeerUnreachable otherwise."""
        said = await self._get(RINGS_FOR.format(slug=slug), {"org": org, "caller": caller})
        try:
            return RingsFor.model_validate(said).handover()
        except ValueError as unreadable:
            raise PeerUnreachable(
                UNREACHABLE.format(url=self._url, said=unreadable)
            ) from unreadable

    async def production_routes_of(self, org: str) -> tuple[Route, ...]:
        """Every door of this org in production, as production's own table holds them."""
        said = await self._get(ROUTES, {"org": org, "env": PRODUCTION})
        try:
            return ROUTES_OF.validate_python(said)
        except ValueError as unreadable:
            raise PeerUnreachable(
                UNREACHABLE.format(url=self._url, said=unreadable)
            ) from unreadable

    async def _get(self, path: str, params: dict[str, str]) -> object:
        """One read on the peer key; anything but a 2xx with JSON is the peer not answering."""
        try:
            answer = await self._http.get(
                f"{self._url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {self._key}"},
                timeout=TIMEOUT_S,
            )
            answer.raise_for_status()
            return answer.json()
        except (httpx.HTTPError, ValueError) as failed:
            raise PeerUnreachable(UNREACHABLE.format(url=self._url, said=failed)) from failed

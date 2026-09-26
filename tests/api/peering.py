"""The other instance, scripted: what a door here asks it on the peer key, and what it answers."""

from collections.abc import Callable, Iterator

import httpx
import pytest

from pinecall.api.app import app
from pinecall.api.ops.peers import the_production, the_sandbox
from pinecall.auth.peers import Peer

# Registered as a plugin by tests/conftest.py: production's side (tests/api/test_fleet_doors.py)
# and the sandbox's (tests/api/agents/test_the_line.py) script the same other instance.

# Where the other instance answers, as this one was told, and the key it minted for this one.
THERE = "https://elsewhere.example.test"
A_PEER_KEY = "pk_test_the_other_instance_minted_for_this_one"


class ThePeer:
    """The other instance's doors: one answer, and every request the peer key made of them."""

    def __init__(self, answer: httpx.Response | Exception) -> None:
        self.answer = answer
        self.asked: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.asked.append(request)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


type Scripting = Callable[[Callable[..., Peer | None], httpx.Response | Exception], ThePeer]


@pytest.fixture
def other_instance() -> Iterator[Scripting]:
    """`other_instance(the_sandbox, answer)`: that door reaches a scripted instance from now on,
    and nobody once the test is over."""

    def scripted(door: Callable[..., Peer | None], answer: httpx.Response | Exception) -> ThePeer:
        peer = ThePeer(answer)
        client = httpx.AsyncClient(transport=httpx.MockTransport(peer.handle))
        app.dependency_overrides[door] = lambda: Peer(client, THERE, A_PEER_KEY)
        return peer

    yield scripted
    for door in (the_production, the_sandbox):
        app.dependency_overrides.pop(door, None)

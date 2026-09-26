"""Which other instance a door here reaches: production's sandbox, a sandbox's production, none."""

from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from starlette.requests import HTTPConnection

from pinecall.api.ops.peers import get_production_peer, get_sandbox_peer
from pinecall.settings import Settings
from tests.conftest import a_sandbox

pytestmark = pytest.mark.unit

SANDBOX_URL = "https://sandbox.example.test"


def a_connection() -> HTTPConnection:
    """The one thing the two deps read off a request: the process's httpx client."""
    gateway = SimpleNamespace(state=SimpleNamespace(http=httpx.AsyncClient()))
    return cast(HTTPConnection, SimpleNamespace(app=gateway))


def test_production_asks_the_sandbox_it_names_and_nobody_when_it_names_none() -> None:
    named = Settings(world="production", sandbox_url=SANDBOX_URL, sandbox_key="k")
    assert get_sandbox_peer(a_connection(), named) is not None
    assert get_sandbox_peer(a_connection(), Settings(world="production")) is None
    assert get_production_peer(a_connection(), named) is None


def test_a_sandbox_asks_the_production_it_signs_people_in_at_once_it_holds_its_key() -> None:
    assert get_production_peer(a_connection(), a_sandbox(peer_key="k")) is not None
    assert get_production_peer(a_connection(), a_sandbox()) is None
    assert get_sandbox_peer(a_connection(), a_sandbox(peer_key="k")) is None

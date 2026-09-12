"""The fixtures about people: the members, the codes, the throttle, and somebody with no key."""

from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.auth.codes import LoginCodes
from pinecall.auth.members import MemoryMembers
from pinecall.auth.pairing import Pairings
from pinecall.auth.throttle import Throttle
from tests.api.conftest import over_the_asgi_app

# Registered as a plugin by tests/conftest.py, beside tests/postgres.py: the api harness wants
# them for its wiring and the CLI suites drive that harness, so they are everybody's.


@pytest.fixture
def members() -> MemoryMembers:
    """The people of the tenants: nobody at the start of a test, and nothing inherited."""
    return MemoryMembers()


@pytest.fixture
def login_codes() -> LoginCodes:
    """The one-use codes minted here: none at the start of a test."""
    return LoginCodes()


@pytest.fixture
def pairings() -> Pairings:
    """The words a terminal printed, waiting for a browser: none at the start of a test."""
    return Pairings()


@pytest.fixture
def throttle() -> Throttle:
    """Who has knocked with a password lately: nobody at the start of a test."""
    return Throttle()


# The one client in the suite that carries no Authorization header at all: the person holding an
# invitation link, and the person logging in. Every other client here knocks with a key.
@pytest.fixture
async def stranger(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    """Somebody with no key: the doors that take none are the only ones they can open."""
    http = over_the_asgi_app("")
    yield http
    await http.aclose()

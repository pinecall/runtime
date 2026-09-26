"""The fixtures about people: the members, the codes, the throttle, and somebody with no key."""

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from pinecall.api.app import app
from pinecall.api.deps import the_signups
from pinecall.auth.keys import MemoryKeys
from pinecall.auth.login_codes import LoginCodes
from pinecall.auth.members_memory import MemoryMembers
from pinecall.auth.pairing import Pairings
from pinecall.auth.signups import PendingSignups
from pinecall.auth.throttle import Throttle
from pinecall.auth.visitor_keys import StandingKeys
from tests.api.conftest import over_the_asgi_app

# Registered as a plugin by tests/conftest.py, beside tests/postgres.py: the api harness wants
# them for its wiring and the CLI suites drive that harness, so they are everybody's.


@pytest.fixture
def members() -> MemoryMembers:
    """The people of the tenants: nobody at the start of a test, and nothing inherited."""
    return MemoryMembers()


# What the doors verify a key against, wired as the lifespan wires it (auth/keys.py, keys_for):
# the table, asking of a visitor's key whether its person still runs the box.
@pytest.fixture
def standing(keys: MemoryKeys, members: MemoryMembers) -> StandingKeys:
    """The keys table as the gateway holds it, over this test's keys and people."""
    return StandingKeys(keys, members)


@pytest.fixture
def login_codes() -> LoginCodes:
    """The one-use codes minted here: none at the start of a test."""
    return LoginCodes()


@pytest.fixture
def signups() -> PendingSignups:
    """The sign-ups waiting on a code: none at the start of a test."""
    return PendingSignups()


# Wired here, as mailing.py wires the outbox, so a test that brings its own `signups` (on its own
# clock) is the one the doors read — the harness's conftest stays under its ceiling.
@pytest.fixture(autouse=True)
def the_signups_wired(signups: PendingSignups) -> Iterator[None]:
    """The doors read this test's pending sign-ups."""
    app.dependency_overrides[the_signups] = lambda: signups
    yield
    app.dependency_overrides.pop(the_signups, None)


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

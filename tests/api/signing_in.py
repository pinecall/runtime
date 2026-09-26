"""The fixtures about signing in at a provider: the org's wiring, the sign-ins, the fake IdP."""

from collections.abc import Iterator

import httpx
import pytest
from cryptography.fernet import Fernet

from pinecall.api.accounts.org_sso import the_handshakes, the_http, the_sso
from pinecall.api.app import app
from pinecall.auth.sso import Handshakes
from pinecall.orgs.sso import MemorySso, Sso
from tests.api.conftest import A_VAULT_KEY
from tests.api.fake_idp import FakeIdp

# Registered as a plugin by tests/conftest.py, beside tests/api/people.py.
#
# These three answer the app themselves rather than through the `wired` fixture, because a sign-in
# at a provider is the only thing that asks for them: a test that exercises one asks for the
# fixture, and every other test in the suite leaves the doors reading what the lifespan put on
# app.state — which is nothing, and nothing is the right answer for `the_sso`. Each pops its own
# override, so nothing of one test is still standing in the next.


@pytest.fixture
def idp() -> FakeIdp:
    """The identity provider an org signs in with, scripted: nothing here reaches a network."""
    return FakeIdp()


@pytest.fixture
def http(idp: FakeIdp) -> Iterator[httpx.AsyncClient]:
    """What the gateway talks to other people's servers with: only the fake provider answers."""
    client = httpx.AsyncClient(transport=idp.transport())
    app.dependency_overrides[the_http] = lambda: client
    yield client
    app.dependency_overrides.pop(the_http, None)


@pytest.fixture
def sso() -> Iterator[Sso | None]:
    """Where an org's identity provider is kept, empty at the start of every test."""
    kept = MemorySso(Fernet(A_VAULT_KEY.encode()))
    app.dependency_overrides[the_sso] = lambda: kept
    yield kept
    app.dependency_overrides.pop(the_sso, None)


@pytest.fixture
def handshakes() -> Iterator[Handshakes]:
    """The sign-ins out at a provider right now: none at the start of a test, none inherited."""
    opened = Handshakes()
    app.dependency_overrides[the_handshakes] = lambda: opened
    yield opened
    app.dependency_overrides.pop(the_handshakes, None)

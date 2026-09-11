"""The fixtures about people: the members table, the login codes and the password throttle."""

import pytest

from pinecall.auth.codes import LoginCodes
from pinecall.auth.members import MemoryMembers
from pinecall.auth.throttle import Throttle

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
def throttle() -> Throttle:
    """Who has knocked with a password lately: nobody at the start of a test."""
    return Throttle()

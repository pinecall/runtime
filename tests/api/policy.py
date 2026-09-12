"""The fixture about policy: the points as the runtime answers them, until a test plugs one in."""

from __future__ import annotations

import pytest

from pinecall.extensions import Extensions

# Registered as a plugin by tests/conftest.py, beside tests/api/people.py and tests/api/carriers.py,
# because `wired` — which every suite that drives the real app uses — asks for it.


@pytest.fixture
def extensions() -> Extensions:
    """No policy plugged in: an org made at the alta may do everything, and no row says so."""
    return Extensions()

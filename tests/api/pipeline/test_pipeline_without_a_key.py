"""A stage whose vendor has no key in this process: the screen says so before the line goes dead."""

from __future__ import annotations

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.agents.registry import Registry
from tests.api.conftest import PIPELINE
from tests.api.pipeline.conftest import declared

pytestmark = pytest.mark.unit


# Ring 0 gives every vendor a dead sentinel key, so a process with NONE has to be asked for on
# purpose. This is the box an operator has just installed and not finished configuring.
@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The same environment the suite builds, with the voice vendor's key taken back out."""
    return settings.model_copy(update={"eleven_api_key": None})


async def test_a_stage_whose_vendor_has_no_key_says_so_instead_of_reading_as_ready(
    fleet_http: httpx.AsyncClient, registry: Registry
) -> None:
    await declared(registry)
    said = (await fleet_http.get(PIPELINE)).json()
    assert said["unavailable_reasons"] == {"speaks": "elevenlabs has no API key in this process"}

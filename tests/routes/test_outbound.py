"""The org's outbound trunk on the SFU: made once by name, and the name is the instance's."""

import pytest

from pinecall._settings import Settings
from pinecall.routes import outbound as outbound_module
from pinecall.routes.outbound import Placing, outbound_for
from tests.routes.sfu import TheSfu

pytestmark = pytest.mark.unit


async def test_the_outbound_trunk_is_named_by_the_instances_fleet_and_found_by_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sfu = TheSfu()
    monkeypatch.setattr(outbound_module.api, "LiveKitAPI", sfu)
    outbound = outbound_for(
        Settings(livekit_api_key="k", livekit_api_secret="s" * 32, fleet="pinecall-sandbox")
    )
    assert outbound is not None
    made = await outbound.provisioned("clinica", Placing(address="pbx.test", numbers=("+1",)))
    assert [trunk.name for trunk in sfu.outbound] == ["pinecall-sandbox:clinica:out"]
    assert await outbound.standing("clinica") == made

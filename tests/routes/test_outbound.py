"""The org's outbound trunk on the SFU: made once by name, and the name is the instance's."""

import pytest
from livekit import api

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
        Settings(
            world="production",
            livekit_api_key="k",
            livekit_api_secret="s" * 32,
            fleet="pinecall-sandbox",
        )
    )
    assert outbound is not None
    made = await outbound.provisioned("clinica", Placing(address="pbx.test", numbers=("+1",)))
    assert [trunk.name for trunk in sfu.outbound] == ["pinecall-sandbox:clinica:out"]
    assert await outbound.standing("clinica") == made


async def test_the_default_fleets_legacy_outbound_trunk_is_found_and_renamed_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dial door finds it before the reconcile renames it, and after, by the new name."""
    sfu = TheSfu()
    monkeypatch.setattr(outbound_module.api, "LiveKitAPI", sfu)
    legacy = await sfu.create_outbound_trunk(
        api.CreateSIPOutboundTrunkRequest(
            trunk=api.SIPOutboundTrunkInfo(name="pinecall-clinica-out")
        )
    )
    outbound = outbound_for(
        Settings(world="production", livekit_api_key="k", livekit_api_secret="s" * 32)
    )
    assert outbound is not None
    assert await outbound.standing("clinica") == legacy.sip_trunk_id
    placing = Placing(address="pbx.test", numbers=("+1",))
    assert await outbound.provisioned("clinica", placing) == legacy.sip_trunk_id
    assert [trunk.name for trunk in sfu.outbound] == ["pinecall:clinica:out"]

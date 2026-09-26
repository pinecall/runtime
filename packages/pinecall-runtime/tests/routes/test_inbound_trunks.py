"""The SFU's trunks: what an import admits, what letting go removes, and whose name each carries."""

import pytest
from livekit import api

from pinecall.routes import inbound_trunks as trunks_module
from pinecall.routes.inbound_trunks import LivekitTrunks, trunks_for
from pinecall.settings import Settings
from tests.routes.fakes import MemoryTrunks
from tests.routes.sfu import TheSfu

pytestmark = pytest.mark.unit


async def test_a_number_is_admitted_onto_the_orgs_one_trunk_and_released_off_it() -> None:
    trunks = MemoryTrunks()
    first = await trunks.admitted("clinica", "+14176743169", ["54.172.60.0/30"], None)
    second = await trunks.admitted("clinica", "+34910000000", ["54.172.60.0/30"], None)
    assert first == second == "ST_clinica", "one trunk per org"
    assert trunks.trunks["clinica"].numbers == {"+14176743169", "+34910000000"}
    assert await trunks.released("clinica", "+34910000000") is True
    assert await trunks.released("clinica", "+34910000000") is False
    assert await trunks.released("tienda", "+1") is False


async def test_a_sip_peers_credentials_ride_the_trunk() -> None:
    trunks = MemoryTrunks()
    await trunks.admitted("clinica", "+1", ["203.0.113.0/24"], ("pbx", "pw"))
    assert trunks.trunks["clinica"].auth == ("pbx", "pw")
    assert trunks.trunks["clinica"].allowed == ("203.0.113.0/24",)


def test_the_real_sfu_needs_the_livekit_pair_and_is_none_without_it() -> None:
    assert (
        trunks_for(Settings(world="production", livekit_api_key=None, livekit_api_secret=None))
        is None
    )
    assert isinstance(
        trunks_for(Settings(world="production", livekit_api_key="k", livekit_api_secret="s" * 32)),
        LivekitTrunks,
    )


@pytest.fixture
def sfu(monkeypatch: pytest.MonkeyPatch) -> TheSfu:
    """Every LiveKitAPI the trunks open is this one SFU, which keeps what it was asked to make."""
    the_sfu = TheSfu()
    monkeypatch.setattr(trunks_module.api, "LiveKitAPI", the_sfu)
    return the_sfu


def an_instance(fleet: str) -> Settings:
    return Settings(
        world="production", livekit_api_key="k", livekit_api_secret="s" * 32, fleet=fleet
    )


async def test_the_orgs_trunk_and_its_rule_are_named_by_the_instances_fleet(sfu: TheSfu) -> None:
    """The SFU is shared and a trunk is found by name: the fleet leads, a colon separates."""
    trunks = trunks_for(an_instance("pinecall-sandbox"))
    assert trunks is not None
    await trunks.admitted("clinica", "+34910000000", ["54.172.60.0/30"], None)
    trunk = sfu.inbound_named("pinecall-sandbox:clinica")
    assert trunk is not None and list(trunk.numbers) == ["+34910000000"]
    [rule] = sfu.rules
    assert rule.name == "pinecall-sandbox:clinica:one-room-per-caller"
    assert [one.agent_name for one in rule.room_config.agents] == ["pinecall-sandbox"]


async def test_two_instances_keep_two_trunks_for_one_org_and_never_adopt_each_others(
    sfu: TheSfu,
) -> None:
    production, sandbox = (
        trunks_for(an_instance("pinecall")),
        trunks_for(an_instance("pinecall-sandbox")),
    )
    assert production is not None and sandbox is not None
    await production.admitted("clinica", "+34910000000", [], None)
    await sandbox.admitted("clinica", "+34910000001", [], None)
    assert sorted(trunk.name for trunk in sfu.inbound) == [
        "pinecall-sandbox:clinica",
        "pinecall:clinica",
    ]
    assert await sandbox.released("clinica", "+34910000000") is False, "production's number"


@pytest.mark.usefixtures("sfu")
async def test_a_number_another_trunk_lists_is_named_by_that_trunk_and_ours_is_not_another() -> (
    None
):
    """livekit-sip refuses an INVITE two trunks list: the import asks this before it admits."""
    production, sandbox = (
        trunks_for(an_instance("pinecall")),
        trunks_for(an_instance("pinecall-sandbox")),
    )
    assert production is not None and sandbox is not None
    await production.admitted("clinica", "+34910000000", [], None)
    assert await sandbox.held_elsewhere("clinica", "+34910000000") == "pinecall:clinica"
    assert await production.held_elsewhere("clinica", "+34910000000") is None
    assert await sandbox.held_elsewhere("clinica", "+34910000001") is None


async def test_the_memory_sfu_names_another_orgs_trunk_and_what_it_was_told_stands_elsewhere() -> (
    None
):
    trunks = MemoryTrunks(elsewhere={"+1": "pinecall-clinica"})
    await trunks.admitted("tienda", "+2", [], None)
    assert await trunks.held_elsewhere("clinica", "+2") == "pinecall:tienda"
    assert await trunks.held_elsewhere("clinica", "+1") == "pinecall-clinica"
    assert await trunks.held_elsewhere("tienda", "+2") is None


async def test_the_default_fleets_legacy_trunk_and_rule_are_renamed_in_place_with_their_numbers(
    sfu: TheSfu,
) -> None:
    """The box's first start after the names changed: one trunk, the same id and numbers, never
    a second one beside it listing the same number — livekit-sip would refuse the call."""
    legacy = await sfu.create_inbound_trunk(
        api.CreateSIPInboundTrunkRequest(
            trunk=api.SIPInboundTrunkInfo(name="pinecall-clinica", numbers=["+34910000000"])
        )
    )
    await sfu.create_dispatch_rule(
        api.CreateSIPDispatchRuleRequest(
            dispatch_rule=api.SIPDispatchRuleInfo(
                name="pinecall-clinica-one-room-per-caller", trunk_ids=[legacy.sip_trunk_id]
            )
        )
    )
    trunks = trunks_for(an_instance("pinecall"))
    assert trunks is not None
    assert await trunks.held_elsewhere("clinica", "+34910000000") is None, "its own, not another's"
    kept = await trunks.admitted("clinica", "+34910000001", [], None)
    [trunk], [rule] = sfu.inbound, sfu.rules
    assert (kept, trunk.name) == (legacy.sip_trunk_id, "pinecall:clinica")
    assert sorted(trunk.numbers) == ["+34910000000", "+34910000001"]
    assert (rule.name, list(rule.trunk_ids)) == (
        "pinecall:clinica:one-room-per-caller",
        [legacy.sip_trunk_id],
    )
    assert [one.agent_name for one in rule.room_config.agents] == ["pinecall"]


async def test_another_fleet_never_adopts_the_default_fleets_legacy_trunk(sfu: TheSfu) -> None:
    await sfu.create_inbound_trunk(
        api.CreateSIPInboundTrunkRequest(
            trunk=api.SIPInboundTrunkInfo(name="pinecall-clinica", numbers=["+34910000000"])
        )
    )
    sandbox = trunks_for(an_instance("pinecall-sandbox"))
    assert sandbox is not None
    assert await sandbox.held_elsewhere("clinica", "+34910000000") == "pinecall-clinica"
    await sandbox.admitted("clinica", "+34910000001", [], None)
    assert sorted(trunk.name for trunk in sfu.inbound) == [
        "pinecall-clinica",
        "pinecall-sandbox:clinica",
    ]

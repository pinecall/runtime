"""The SFU's trunks as a dict: what an import admits, and what letting go removes."""

import pytest

from pinecall._settings import Settings
from pinecall.routes.trunks import LivekitTrunks, MemoryTrunks, trunks_for

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
    assert trunks_for(Settings(livekit_api_key=None, livekit_api_secret=None)) is None
    assert isinstance(
        trunks_for(Settings(livekit_api_key="k", livekit_api_secret="s" * 32)), LivekitTrunks
    )

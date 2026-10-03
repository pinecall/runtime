"""Tests for the networks 5060 opens to beyond Twilio's, and the file nftables reads them from."""

from pathlib import Path

from pinecall.channels.telephony import carrier_catalog, firewall
from pinecall.channels.telephony.carrier import TWILIO_SIGNALLING
from pinecall.postgres.pool import Pool
from pinecall.tenancy import carrier_networks, orgs
from tests.conftest import postgres


@postgres
async def test_the_fence_opens_to_admitted_carriers_and_approved_asks_alone(pool: Pool) -> None:
    assert await firewall.openings(pool) == []
    await carrier_catalog.admit(pool, "telnyx", on=True)
    org = await orgs.create(pool, "clinica", "Clínica")
    approved, waiting = await carrier_networks.ask(
        pool, org.id, "pbx", ["45.60.12.7", "45.60.12.8"]
    )
    await carrier_networks.decide(pool, approved.id, "approved", "ana@pinecall.test")
    found = await firewall.openings(pool)
    telnyx = carrier_catalog.known_carrier("telnyx").networks
    assert [opening.network for opening in found] == [*telnyx, "45.60.12.7/32"]
    assert found[-1].reason == "clinica: pbx"
    assert waiting.network not in firewall.every_network(found)
    assert firewall.every_network(found)[: len(TWILIO_SIGNALLING)] == list(TWILIO_SIGNALLING)


@postgres
async def test_apply_writes_the_file_and_reloads_only_when_it_changed(
    pool: Pool, tmp_path: Path
) -> None:
    reloads: list[str] = []
    path = tmp_path / "carriers.nft"
    assert await firewall.last_applied(pool) is None
    first = await firewall.apply(pool, path, lambda: reloads.append("nft"))
    assert first.networks == 0
    assert "add element" not in path.read_text(encoding="utf-8")
    await carrier_catalog.admit(pool, "vonage", on=True)
    second = await firewall.apply(pool, path, lambda: reloads.append("nft"))
    await firewall.apply(pool, path, lambda: reloads.append("nft"))
    written = path.read_text(encoding="utf-8")
    assert second.networks == len(carrier_catalog.known_carrier("vonage").networks)
    assert "add element inet pinecall carrier_signalling { 216.147.0.1/32, " in written
    assert "# 216.147.0.1/32 vonage" in written
    assert reloads == ["nft", "nft"]
    kept = await firewall.last_applied(pool)
    assert kept is not None
    assert kept.networks == second.networks

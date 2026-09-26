"""An SFU that lost its trunks gets them back from the tables; one that has them is left alone."""

import pytest
from cryptography.fernet import Fernet

from pinecall.orgs.carriers_memory import MemoryCarriers
from pinecall.orgs.outbound_credentials_memory import MemoryOutboundTrunks
from pinecall.orgs.records_memory import MemoryOrgs
from pinecall.orgs.vault import build_cipher
from pinecall.routes.records_memory import MemoryRoutes
from pinecall.routes.twilio import TWILIO_SIGNALLING
from pinecall.telephony.rebuilding import reconcile_sip
from pinecall.types import Carrier, OutboundTrunk, Route, SipPeer, TwilioAccount
from pinecall_testkit.fake_media import MemoryOutbound, MemoryTrunks

pytestmark = pytest.mark.unit

A_KEY = Fernet.generate_key().decode()
CLINICA = "org_clinica"
TIENDA = "org_tienda"


async def a_world() -> tuple[MemoryOrgs, MemoryCarriers, MemoryRoutes, MemoryOutboundTrunks]:
    """Two orgs with numbers: one on Twilio with an outbound trunk, one on its own SIP peer."""
    orgs = MemoryOrgs()
    for slug in ("clinica", "tienda"):
        await orgs.create(slug, slug.title())
    cipher = build_cipher(A_KEY)
    carriers = MemoryCarriers(cipher)
    await carriers.put(
        Carrier(
            org=await _id(orgs, "clinica"),
            account=TwilioAccount("AC" + "0" * 32, "SK" + "0" * 32, "s" * 32),
        )
    )
    peer = SipPeer(
        username="pbx",
        password="pw",
        addresses=("203.0.113.0/24",),
        outbound_host="pbx.example",
        outbound_transport="tcp",
    )
    await carriers.put(Carrier(org=await _id(orgs, "tienda"), account=peer))
    routes = MemoryRoutes()
    await routes.put(
        Route(org=await _id(orgs, "clinica"), agent="sofia", channel="phone", number="+18604131735")
    )
    await routes.put(
        Route(
            org=await _id(orgs, "clinica"),
            agent="sofia",
            channel="phone",
            number="+14176743169",
            env="sandbox",
        )
    )
    await routes.put(
        Route(org=await _id(orgs, "tienda"), agent="ana", channel="phone", number="+34910000000")
    )
    outbound_rows = MemoryOutboundTrunks(cipher)
    await outbound_rows.put(
        OutboundTrunk(
            org=await _id(orgs, "clinica"),
            kind="twilio",
            trunk_id="ST_lost",
            address="clinica.pstn.twilio.com",
            username="pinecall-clinica",
            password="secret",
        )
    )
    return orgs, carriers, routes, outbound_rows


async def _id(orgs: MemoryOrgs, slug: str) -> str:
    found = await orgs.find(slug)
    assert found is not None
    return found.id


async def test_an_sfu_with_nothing_on_it_gets_every_trunk_the_tables_know() -> None:
    orgs, carriers, routes, rows = await a_world()
    trunks, outbound = MemoryTrunks(), MemoryOutbound()

    found = await reconcile_sip(orgs, carriers, routes, trunks, rows, outbound)

    clinica, tienda = await _id(orgs, "clinica"), await _id(orgs, "tienda")
    assert set(found.inbound) == {clinica, tienda}
    assert trunks.trunks[clinica].numbers == {"+18604131735", "+14176743169"}, (
        "both worlds' numbers"
    )
    assert (
        trunks.trunks[clinica].allowed == TWILIO_SIGNALLING and trunks.trunks[clinica].auth is None
    )
    assert trunks.trunks[tienda].auth == ("pbx", "pw"), "a peer's trunk keeps its credentials"
    # The outbound trunk came back under a new id, and the row the dial door reads now names it.
    assert found.outbound == (clinica,) and found.renumbered == (clinica,)
    placed = outbound.trunks[clinica]
    assert placed.placing.address == "clinica.pstn.twilio.com"
    assert placed.placing.auth == ("pinecall-clinica", "secret")
    kept = await rows.of(clinica)
    assert kept is not None and kept.trunk_id == placed.trunk_id != "ST_lost"


async def test_an_sfu_that_has_everything_is_left_as_it_was() -> None:
    orgs, carriers, routes, rows = await a_world()
    trunks, outbound = MemoryTrunks(), MemoryOutbound()
    first = await reconcile_sip(orgs, carriers, routes, trunks, rows, outbound)
    before = {org: dict(vars(placed)) for org, placed in outbound.trunks.items()}

    again = await reconcile_sip(orgs, carriers, routes, trunks, rows, outbound)

    assert again.renumbered == () and first.renumbered != ()
    assert {org: dict(vars(placed)) for org, placed in outbound.trunks.items()} == before
    assert len(trunks.trunks) == 2, "one trunk per org, however many passes"


async def test_an_org_with_no_carrier_or_no_number_is_not_an_org_the_sfu_hears_about() -> None:
    orgs = MemoryOrgs()
    await orgs.create("silent", "Silent")
    carriers = MemoryCarriers(build_cipher(A_KEY))
    trunks = MemoryTrunks()

    found = await reconcile_sip(orgs, carriers, MemoryRoutes(), trunks, None, None)

    assert found.inbound == () and trunks.trunks == {}

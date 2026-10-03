"""Tests for what an account is to telephony: its fence, where it is dialled, its API."""

import httpx
import pytest

from pinecall.channels.telephony import carrier
from pinecall.channels.telephony._twilio import Twilio
from pinecall.domain.errors import Conflict, DeclarationRefused
from pinecall.tenancy.carriers import Carrier, Termination, TwilioAccount, WhatsappAccount
from tests.channels.conftest import a_peer
from tests.fakes.idp import a_sid

A_NUMBER = "+59829001199"

A_TERMINATION = Termination.model_validate(
    {"host": "box-1234.pstn.twilio.com", "username": "pinecall-org-a", "password": "minted here"}
)


def a_twilio(*, outbound: Termination | None = None) -> Carrier:
    """The org's Twilio account, dialable once a termination was made."""
    account = TwilioAccount.model_validate(
        {"account_sid": a_sid("AC", 1), "user": a_sid("SK", 1), "secret": "the key's secret"}
    )
    return Carrier(org="org_a", account=account, outbound=outbound)


def a_whatsapp() -> Carrier:
    """The org's WhatsApp number at Meta."""
    return Carrier(
        org="org_a",
        account=WhatsappAccount.model_validate(
            {"phone_number_id": "1171462246041897", "access_token": "a token"}
        ),
    )


def test_a_twilio_number_and_one_with_no_account_are_fenced_to_twilios_edges() -> None:
    for brought in (a_twilio(), None):
        fence = carrier.fence_of("org_a", A_NUMBER, brought, ())
        assert (fence.trunk, fence.networks) == ("org_a", carrier.TWILIO_SIGNALLING)


def test_a_peer_is_fenced_to_its_own_networks_with_its_pair() -> None:
    fence = carrier.fence_of("org_a", A_NUMBER, Carrier("org_a", a_peer()), ())
    assert (fence.trunk, fence.networks, fence.username) == (
        "org_a:pbx",
        ("203.0.113.0/24",),
        "pbx",
    )


def test_a_hooked_number_with_networks_is_a_fence_of_its_own() -> None:
    fence = carrier.fence_of("org_a", A_NUMBER, None, ("198.51.100.7/32",))
    assert (fence.trunk, fence.networks) == (f"org_a:{A_NUMBER}", ("198.51.100.7/32",))


def test_a_whatsapp_number_is_on_no_trunk() -> None:
    with pytest.raises(DeclarationRefused, match="on no SIP trunk"):
        carrier.fence_of("org_a", A_NUMBER, a_whatsapp(), ())


def test_what_each_kind_lacks_to_dial_out() -> None:
    assert "POST /v1/carrier/outbound" in carrier.missing_to_dial(a_twilio())[0]
    assert "outbound_host" in carrier.missing_to_dial(Carrier("org_a", a_peer()))[0]
    assert "places no call" in carrier.missing_to_dial(a_whatsapp())[0]
    dialable = A_TERMINATION
    assert carrier.missing_to_dial(a_twilio(outbound=dialable)) == []


def test_a_call_goes_to_twilios_termination_or_to_the_peers_host() -> None:
    dialable = A_TERMINATION
    through_twilio = carrier.dialled_of(a_twilio(outbound=dialable))
    assert (through_twilio.hostname, through_twilio.transport) == (dialable.host, "auto")
    peer = Carrier("org_a", a_peer(outbound_host="sip.carrier.example", outbound_transport="tls"))
    through_peer = carrier.dialled_of(peer)
    assert (through_peer.hostname, through_peer.transport, through_peer.username) == (
        "sip.carrier.example",
        "tls",
        "pbx",
    )
    with pytest.raises(Conflict, match="outbound_host"):
        carrier.dialled_of(Carrier("org_a", a_peer()))


async def test_only_twilio_has_an_api_the_box_drives_and_only_meta_is_whatsapp() -> None:
    async with httpx.AsyncClient() as http:
        assert isinstance(carrier.control_of(http, a_twilio()), Twilio)
        assert carrier.control_of(http, Carrier("org_a", a_peer())) is None
        assert carrier.control_of(http, a_whatsapp()) is None
        await carrier.verify_account(http, a_peer())
    assert carrier.meta_of(a_whatsapp()) is not None
    assert carrier.meta_of(a_twilio()) is None

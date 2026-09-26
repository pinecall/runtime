"""A carrier is one org's Twilio account or SIP peer, checked before any account is touched."""

import pytest

from pinecall.types import Carrier, DeclarationRefused, SipPeer, TwilioAccount, parse_carrier_kind

pytestmark = pytest.mark.unit

A_SID = "AC" + "0" * 32
A_KEY_SID = "SK" + "1" * 32


def test_a_twilio_carrier_names_its_account_and_never_its_secret() -> None:
    carrier = Carrier(org="clinica", account=TwilioAccount(A_SID, A_KEY_SID, "s3cret"))
    assert (carrier.kind, carrier.named) == ("twilio", A_SID)


def test_a_sip_peer_names_its_user_and_its_networks_are_cidrs() -> None:
    carrier = Carrier(org="clinica", account=SipPeer("clinica", "pw", ("203.0.113.0/24",)))
    assert (carrier.kind, carrier.named) == ("sip", "clinica")
    with pytest.raises(DeclarationRefused, match="CIDR"):
        SipPeer("clinica", "pw", ("not-a-network",))
    with pytest.raises(DeclarationRefused, match="at least one network"):
        SipPeer("clinica", "pw", ())


@pytest.mark.parametrize("sid", ["AC123", "XX" + "0" * 32, ""])
def test_a_twilio_sid_that_is_not_one_is_refused(sid: str) -> None:
    with pytest.raises(DeclarationRefused, match="34 characters"):
        TwilioAccount(sid, sid or A_SID, "s3cret")


def test_a_kind_that_is_neither_is_refused_with_the_two() -> None:
    assert parse_carrier_kind("sip") == "sip"
    with pytest.raises(DeclarationRefused, match=r"sip.*twilio"):
        parse_carrier_kind("vonage")

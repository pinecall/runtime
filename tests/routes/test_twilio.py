"""Twilio over httpx, answered by a transport that is a dict; the fence agrees on the networks."""

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from pinecall.routes.twilio import (
    ACCOUNTS_API,
    CARRIER_TRUNK,
    TRUNKING_API,
    TWILIO_SIGNALLING,
    HttpTwilio,
    Trunk,
    TwilioRefused,
    origination_uri,
    termination_label,
)
from pinecall.types import TwilioAccount

pytestmark = pytest.mark.unit

A_SID = "AC" + "0" * 32
ACCOUNT = TwilioAccount(A_SID, A_SID, "the-auth-token")
FENCE = Path(__file__).resolve().parents[2] / "infra" / "box" / "nftables.conf"


class _Twilio:
    """Twilio's REST API as a dict: what it owns, and every write it was sent."""

    def __init__(self) -> None:
        self.writes: list[tuple[str, dict[str, str]]] = []
        self.origination: list[dict[str, str]] = []
        self.opens = True

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if not self.opens:
            return httpx.Response(401, json={"message": "Authenticate"})
        if request.method == "POST":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            self.writes.append((url, form))
            if url.endswith("/Trunks"):
                return httpx.Response(
                    201, json={"sid": "TK_1", "friendly_name": form["FriendlyName"]}
                )
            if "/OriginationUrls" in url:
                self.origination = [{"sid": "OU_1", "sip_url": form["SipUrl"]}]
                return httpx.Response(201, json={"sid": "OU_1", "sip_url": form["SipUrl"]})
            if url.endswith("/IncomingPhoneNumbers.json"):
                return httpx.Response(
                    201, json={"sid": "PN_9", "phone_number": form["PhoneNumber"]}
                )
            return httpx.Response(201, json={"sid": "PN_1"})
        if "/AvailablePhoneNumbers/US/Local.json" in url:
            for_sale = [{"phone_number": "+14175550100"}] if "AreaCode=417" in url else []
            return httpx.Response(200, json={"available_phone_numbers": for_sale})
        if url.endswith(f"/Accounts/{A_SID}.json"):
            return httpx.Response(200, json={"friendly_name": "Clínica Norte"})
        if "IncomingPhoneNumbers" in url:
            return httpx.Response(
                200,
                json={
                    "incoming_phone_numbers": [
                        {"sid": "PN_1", "phone_number": "+14176743169", "friendly_name": "abai"}
                    ]
                },
            )
        if url.endswith("/Trunks?PageSize=50"):
            return httpx.Response(
                200, json={"trunks": [{"sid": "TK_1", "friendly_name": "pinecall-clinica"}]}
            )
        if "/OriginationUrls" in url:
            return httpx.Response(200, json={"origination_urls": self.origination})
        if "/PhoneNumbers" in url:
            return httpx.Response(200, json={"phone_numbers": [{"phone_number": "+14176743169"}]})
        return httpx.Response(404, json={"message": f"nothing at {url}"})


def a_client(fake: _Twilio) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))


async def test_the_account_is_verified_and_its_numbers_and_trunk_are_read() -> None:
    fake = _Twilio()
    twilio = HttpTwilio(a_client(fake), ACCOUNT)
    assert await twilio.verified() == "Clínica Norte"
    (number,) = await twilio.numbers()
    assert (number.sid, number.number, number.name) == ("PN_1", "+14176743169", "abai")
    trunk = await twilio.trunk_named("pinecall-clinica")
    assert trunk == Trunk(sid="TK_1", name="pinecall-clinica", origination=())
    assert await twilio.trunk_named("nobody") is None
    assert await twilio.numbers_on("TK_1") == ("+14176743169",)


async def test_pointing_creates_the_one_origination_uri_then_replaces_it_in_place() -> None:
    fake = _Twilio()
    twilio = HttpTwilio(a_client(fake), ACCOUNT)
    trunk = Trunk(sid="TK_1", name="pinecall-clinica", origination=())
    await twilio.pointed_at(trunk, origination_uri("box.pinecall.io"))
    assert fake.writes[-1][0] == f"{TRUNKING_API}/Trunks/TK_1/OriginationUrls"
    assert fake.writes[-1][1]["SipUrl"] == "sip:box.pinecall.io:5060;transport=udp"
    await twilio.pointed_at(trunk, origination_uri("other.pinecall.io"))
    assert fake.writes[-1][0].endswith("/OriginationUrls/OU_1"), "replaced, never doubled"
    fake.origination = [{"sid": "a", "sip_url": "x"}, {"sid": "b", "sip_url": "y"}]
    with pytest.raises(TwilioRefused, match="2 origination URIs"):
        await twilio.pointed_at(trunk, "sip:z")


async def test_wrong_credentials_verify_as_none_and_every_other_read_is_a_refusal() -> None:
    fake = _Twilio()
    fake.opens = False
    twilio = HttpTwilio(a_client(fake), ACCOUNT)
    assert await twilio.verified() is None
    with pytest.raises(TwilioRefused, match="twilio 401"):
        await twilio.numbers()


async def test_a_number_is_attached_by_its_sid() -> None:
    fake = _Twilio()
    await HttpTwilio(a_client(fake), ACCOUNT).attached("TK_1", "PN_1")
    assert fake.writes[-1] == (
        f"{TRUNKING_API}/Trunks/TK_1/PhoneNumbers",
        {"PhoneNumberSid": "PN_1"},
    )


async def test_shopping_asks_for_one_local_voice_number_and_buying_is_one_post() -> None:
    fake = _Twilio()
    twilio = HttpTwilio(a_client(fake), ACCOUNT)
    assert await twilio.for_sale("us", "417") == "+14175550100"
    assert await twilio.for_sale("US", "999") is None
    bought = await twilio.bought("+14175550100")
    assert (bought.sid, bought.number) == ("PN_9", "+14175550100")
    assert fake.writes[-1] == (
        f"{ACCOUNTS_API}/Accounts/{A_SID}/IncomingPhoneNumbers.json",
        {"PhoneNumber": "+14175550100"},
    )


async def test_the_termination_is_sent_as_the_whole_host_twilio_demands() -> None:
    # Twilio refuses a bare label (21245: "the hostname must end with twilio.com").
    fake = _Twilio()
    await HttpTwilio(a_client(fake), ACCOUNT).terminating("TK_1", "pinecall-clinica")
    assert fake.writes == [
        (f"{TRUNKING_API}/Trunks/TK_1", {"DomainName": "pinecall-clinica.pstn.twilio.com"})
    ]


def test_the_networks_the_trunk_admits_are_the_ones_the_fence_opens() -> None:
    """One list in two places would be a call the fence drops; this holds them to be one."""
    fence = FENCE.read_text(encoding="utf-8")
    inside = fence.split("set carrier_signalling", 1)[1].split("}", 1)[0]
    listed: Any = [line.strip().rstrip(",") for line in inside.splitlines() if "/" in line]
    assert sorted(listed) == sorted(TWILIO_SIGNALLING)
    assert json.dumps(TWILIO_SIGNALLING)  # the tuple is plain data, as the trunk wants it


def test_the_default_fleets_carrier_trunk_keeps_the_name_every_production_trunk_already_has() -> (
    None
):
    assert CARRIER_TRUNK.format(fleet="pinecall", org="org_1a2b") == "pinecall-org_1a2b"
    assert termination_label("pinecall", "org_1a2b") == "pinecall-org-1a2b"


def test_the_sandboxs_trunk_and_label_stand_beside_productions_on_one_account() -> None:
    """One tenant account serves both instances: two trunks, two labels, neither the other's."""
    names = {
        CARRIER_TRUNK.format(fleet=fleet, org="org_1a2b")
        for fleet in ("pinecall", "pinecall-sandbox")
    }
    labels = {termination_label(fleet, "org_1a2b") for fleet in ("pinecall", "pinecall-sandbox")}
    assert (len(names), len(labels)) == (2, 2)
    assert labels == {"pinecall-org-1a2b", "pinecall-sandbox-org-1a2b"}

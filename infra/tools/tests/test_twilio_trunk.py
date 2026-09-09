"""The trunk script's promises: a trunk made once, adopted in one write, planned with none."""

from dataclasses import dataclass, field
from typing import Any, override

import pytest
from carrier_cidrs import signalling_cidrs
from twilio_rest import Twilio
from twilio_trunk import carrier_trunk, main, parse_arguments, the_plan, wire

pytestmark = pytest.mark.unit

A_NUMBER = "+59829000000"
A_RUN = ["--number", A_NUMBER, "--sip-host", "box.example"]
AN_ADOPTION = [*A_RUN, "--adopt"]

A_STANDING_TRUNK = {
    "sid": "TK-already",
    "friendly_name": "pinecall",
    "transfer_mode": "disable-all",
}
A_URI_POINTING_ELSEWHERE = {
    "sid": "OU-elsewhere",
    "friendly_name": "somebody-elses-box",
    "sip_url": "sip:gone.example;transport=udp",
}
HERE = "sip:box.example:5060;transport=udp"


@dataclass(frozen=True)
class ATwilioThatWritesNothingDown(Twilio):
    """A carrier that answers what the test hands it, and keeps every write it was asked for."""

    trunks: list[dict[str, Any]]
    numbers: list[dict[str, Any]]
    origination_urls: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    on_the_trunk: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    written: list[str] = field(default_factory=list[str])
    forms: list[dict[str, str]] = field(default_factory=list[dict[str, str]])

    @override
    def get(self, url: str) -> dict[str, Any]:
        """The four reads the script makes, told apart by the resource in the URL."""
        if "IncomingPhoneNumbers" in url:
            return {"incoming_phone_numbers": self.numbers}
        if "OriginationUrls" in url:
            return {"origination_urls": self.origination_urls}
        if "PhoneNumbers" in url:
            return {"phone_numbers": self.on_the_trunk}
        return {"trunks": self.trunks}

    @override
    def post(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        """Nothing is sent anywhere: the URL is written down so a test can say it happened."""
        self.written.append(url)
        self.forms.append(form)
        return {
            "sid": "TK-created",
            "friendly_name": form.get("FriendlyName", ""),
            "sip_url": form.get("SipUrl", ""),
        }


def a_carrier(
    trunks: list[dict[str, Any]],
    numbers: list[dict[str, Any]],
    origination_urls: list[dict[str, Any]] | None = None,
    on_the_trunk: list[dict[str, Any]] | None = None,
) -> ATwilioThatWritesNothingDown:
    """One account, with whatever the test says it already carries."""
    return ATwilioThatWritesNothingDown(
        "AC-test",
        "AC-test",
        "token",
        trunks,
        numbers,
        origination_urls or [],
        on_the_trunk or [],
    )


def an_account_ready_to_be_adopted() -> ATwilioThatWritesNothingDown:
    """The shape this card actually met: a trunk that owns the number and points somewhere dead."""
    return a_carrier(
        trunks=[A_STANDING_TRUNK],
        numbers=[],
        origination_urls=[A_URI_POINTING_ELSEWHERE],
        on_the_trunk=[{"sid": "PN-one", "phone_number": A_NUMBER}],
    )


def test_a_trunk_of_that_name_already_standing_stops_the_run_and_creates_nothing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    twilio = a_carrier(trunks=[A_STANDING_TRUNK], numbers=[{"sid": "PN-one"}])
    assert wire(twilio, parse_arguments(A_RUN)) == 0
    assert twilio.written == []
    printed = capsys.readouterr().out
    assert "TK-already" in printed
    assert "already there" in printed


def test_the_run_that_is_stopped_still_says_whether_a_transfer_would_be_carried(
    capsys: pytest.CaptureFixture[str],
) -> None:
    wire(a_carrier(trunks=[A_STANDING_TRUNK], numbers=[]), parse_arguments(A_RUN))
    printed = capsys.readouterr().out
    assert "a cold transfer will fail" in printed
    assert "--transfer-mode enable-all" in printed


def test_a_number_the_account_does_not_own_stops_before_any_trunk_is_created() -> None:
    twilio = a_carrier(trunks=[], numbers=[])
    assert wire(twilio, parse_arguments(A_RUN)) == 0
    assert twilio.written == []


def test_the_origination_uri_names_the_port_the_box_actually_listens_on() -> None:
    assert f"SipUrl={HERE}" in "\n".join(the_plan(parse_arguments(A_RUN)))


def test_adopting_moves_the_one_origination_uri_and_writes_nothing_else(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The LiveKit half opens a socket, so every adoption test drives the carrier half directly:
    # what they are about is how many requests left for Twilio, and which ones.
    twilio = an_account_ready_to_be_adopted()
    assert carrier_trunk(twilio, parse_arguments(AN_ADOPTION)) == "TK-already"
    assert twilio.written == [
        "https://trunking.twilio.com/v1/Trunks/TK-already/OriginationUrls/OU-elsewhere"
    ]
    assert twilio.forms[0]["SipUrl"] == HERE
    printed = capsys.readouterr().out
    assert "sip:gone.example;transport=udp  — before" in printed
    assert f"{HERE}  — after" in printed


def test_adopting_a_trunk_this_account_does_not_have_creates_nothing() -> None:
    twilio = a_carrier(trunks=[], numbers=[{"sid": "PN-one"}])
    assert carrier_trunk(twilio, parse_arguments(AN_ADOPTION)) is None
    assert twilio.written == []


def test_adopting_a_trunk_the_number_is_not_on_attaches_nothing() -> None:
    twilio = a_carrier(
        trunks=[A_STANDING_TRUNK],
        numbers=[{"sid": "PN-one"}],
        origination_urls=[A_URI_POINTING_ELSEWHERE],
        on_the_trunk=[{"sid": "PN-other", "phone_number": "+59829000099"}],
    )
    assert carrier_trunk(twilio, parse_arguments(AN_ADOPTION)) is None
    assert twilio.written == []


def test_a_trunk_with_two_origination_uris_is_a_choice_the_script_refuses_to_make() -> None:
    twilio = a_carrier(
        trunks=[A_STANDING_TRUNK],
        numbers=[],
        origination_urls=[A_URI_POINTING_ELSEWHERE, {**A_URI_POINTING_ELSEWHERE, "sid": "OU-two"}],
        on_the_trunk=[{"sid": "PN-one", "phone_number": A_NUMBER}],
    )
    assert carrier_trunk(twilio, parse_arguments(AN_ADOPTION)) is None
    assert twilio.written == []


def test_the_plan_names_every_call_the_run_would_make() -> None:
    plan = "\n".join(the_plan(parse_arguments(A_RUN)))
    assert "POST     https://trunking.twilio.com/v1/Trunks  FriendlyName=pinecall" in plan
    assert f"OriginationUrls  SipUrl={HERE}" in plan
    assert "CreateSIPInboundTrunk" in plan
    assert "CreateSIPDispatchRule" in plan


def test_the_adoption_plan_promises_one_write_and_names_no_create() -> None:
    plan = "\n".join(the_plan(parse_arguments(AN_ADOPTION)))
    assert f"OriginationUrls/<uri>  SipUrl={HERE}" in plan
    assert "--adopt never creates" in plan
    assert "POST     https://trunking.twilio.com/v1/Trunks  FriendlyName" not in plan
    assert "PhoneNumberSid" not in plan


def test_the_allow_list_the_plan_prints_is_the_one_the_firewall_reads() -> None:
    plan = "\n".join(the_plan(parse_arguments(A_RUN)))
    assert f"allowed_addresses={','.join(signalling_cidrs())}" in plan


def test_the_dry_run_asks_for_no_credential_because_it_opens_no_socket(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for named in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_API_KEY", "TWILIO_API_SECRET"):
        monkeypatch.delenv(named, raising=False)
    assert main([*A_RUN, "--dry-run"]) == 0
    assert "POST" in capsys.readouterr().out

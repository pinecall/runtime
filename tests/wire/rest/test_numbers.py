"""Tests for the bodies of the number doors."""

from pinecall.wire.rest.numbers import DialRequest, DialResponse, ImportNumberRequest


def test_a_dial_takes_the_number_shown_by_its_wire_name_from() -> None:
    dial = DialRequest.read({"to": "+34600000001", "from": "+13617334133"}, "dial")
    assert dial.from_ == "+13617334133"
    assert dial.written()["from"] == "+13617334133"


def test_an_import_unsaid_answers_the_phone_through_the_orgs_only_account() -> None:
    wanted = ImportNumberRequest.read({"number": "+13617334133", "agent": "front-desk"}, "import")
    assert (wanted.channel, wanted.account, wanted.hooked, wanted.networks) == (
        "phone",
        None,
        False,
        [],
    )


def test_a_dial_answered_says_the_number_shown_as_from() -> None:
    answer = DialResponse.model_validate(
        {
            "call": "call_1",
            "agent": "front-desk",
            "to": "+34600000001",
            "from": "+13617334133",
            "env": "sandbox",
            "log_token": "t",
        }
    )
    assert answer.written()["from"] == "+13617334133"

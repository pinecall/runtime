"""Tests for the bodies of the number doors."""

import pytest
from pydantic import ValidationError

from pinecall.wire.rest.numbers import (
    BuyNumberRequest,
    DialRequest,
    DialResponse,
    ImportNumberRequest,
)


def test_a_dial_takes_the_number_shown_by_its_wire_name_from() -> None:
    dial = DialRequest.read({"to": "+34600000001", "from": "+15550100133"}, "dial")
    assert dial.from_ == "+15550100133"
    assert dial.written()["from"] == "+15550100133"


def test_an_import_unsaid_answers_the_phone_through_the_orgs_only_account() -> None:
    wanted = ImportNumberRequest.read({"number": "+15550100133", "agent": "front-desk"}, "import")
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
            "from": "+15550100133",
            "env": "sandbox",
            "log_token": "t",
        }
    )
    assert answer.written()["from"] == "+15550100133"


# Both ride a URL path the box signs with its own account: two letters, and digits.
def test_a_number_bought_names_a_country_of_two_letters_and_an_area_code_of_digits() -> None:
    assert BuyNumberRequest(country="uy", area_code="2", agent="a").country == "uy"
    for wrong in ({"country": ".."}, {"country": "U1"}, {"country": "US", "area_code": "../x"}):
        with pytest.raises(ValidationError):
            BuyNumberRequest.model_validate({"agent": "a", **wrong})

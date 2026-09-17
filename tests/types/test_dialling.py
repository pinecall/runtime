"""A destination, the country it reaches, and the policy an org dials under."""

from __future__ import annotations

import pytest

from pinecall.types import (
    CALLING_CODES,
    NEVER_DIALLED,
    DeclarationRefused,
    DialPolicy,
    a_destination,
    a_sip_transport,
    calling_code,
)

pytestmark = pytest.mark.unit


# The one-digit codes are what makes longest match safe: no assigned code begins with 1 or 7 and
# is longer than one digit, so the Bahamas is +1 and never a country of its own.
@pytest.mark.parametrize(
    ("number", "code"),
    [
        ("+34910000000", "34"),
        ("+12125550123", "1"),
        ("+12425550123", "1"),
        ("+79001234567", "7"),
        ("+59899111111", "598"),
        ("+442071838750", "44"),
        ("+9999999999", None),
    ],
)
def test_a_number_reaches_the_country_its_longest_code_names(number: str, code: str | None) -> None:
    assert calling_code(number) == code


def test_every_code_is_one_two_or_three_digits_and_nothing_else() -> None:
    """The table is a closed list, and a typo in it would make one country unreachable for good."""
    assert all(code.isdigit() and 1 <= len(code) <= 3 for code in CALLING_CODES)
    assert {"1", "7"} <= CALLING_CODES
    assert all(code in CALLING_CODES for code in NEVER_DIALLED)


@pytest.mark.parametrize(
    ("number", "why"),
    [
        ("600123456", "E.164"),
        ("+882345678901", "satellite or global-service"),
        ("+8701234567", "satellite or global-service"),
        ("+9791234567", "satellite or global-service"),
        ("+99912345", "no country calling code"),
        ("+341234", "shorter than a number anybody calls from"),
    ],
)
def test_a_destination_nobody_could_call_back_is_refused_saying_which(
    number: str, why: str
) -> None:
    with pytest.raises(DeclarationRefused, match=why):
        a_destination(number)


def test_a_destination_carries_the_country_and_what_is_left_of_the_number() -> None:
    spain = a_destination(" +34910000000 ")
    assert (spain.number, spain.code, spain.national) == ("+34910000000", "34", "910000000")


# Empty is not "anywhere": it is the codes of the org's own numbers, worked out at each dial.
def test_an_org_that_named_no_countries_reaches_the_ones_its_numbers_are_in() -> None:
    standing = DialPolicy()
    assert standing.reaches(a_destination("+34600123456"), own=("34",))
    assert not standing.reaches(a_destination("+12125550123"), own=("34",))
    named = DialPolicy(countries=("1",))
    assert named.reaches(a_destination("+12125550123"), own=("34",))
    assert not named.reaches(a_destination("+34600123456"), own=("34",))


def test_the_defaults_are_a_call_back_box_and_not_a_call_centre() -> None:
    standing = DialPolicy()
    assert standing.dial_anywhere is False
    assert (standing.per_minute, standing.per_day, standing.max_duration_s) == (6, 200, 600)


def test_a_guard_is_a_count_and_cannot_be_negative() -> None:
    with pytest.raises(DeclarationRefused, match="per_minute"):
        DialPolicy(per_minute=-1)
    with pytest.raises(DeclarationRefused, match="per_day"):
        DialPolicy(per_day=-1)
    with pytest.raises(DeclarationRefused, match="max_duration_s"):
        DialPolicy(max_duration_s=-1)


def test_a_country_nobody_assigns_is_refused_when_the_policy_is_declared() -> None:
    with pytest.raises(DeclarationRefused, match="no country calling code"):
        DialPolicy(countries=("999",))


def test_the_transport_is_one_of_four_and_unsaid_is_auto() -> None:
    assert a_sip_transport(None) == "auto"
    assert a_sip_transport("tls") == "tls"
    with pytest.raises(DeclarationRefused, match="dialled over one of"):
        a_sip_transport("carrier-pigeon")

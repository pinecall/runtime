"""Tests for the E.164 calling codes and the ranges a box never dials."""

import re

import pytest

from pinecall.domain import country_codes
from pinecall.domain.errors import DeclarationRefused
from pinecall.tenancy import dial_policy


@pytest.mark.parametrize(
    ("number", "why"),
    [
        ("0099000001", "E.164"),
        ("+8816000000", "satellite"),
        ("+59899", "shorter"),
        ("+2100000000", "no country calling code"),
    ],
)
def test_a_destination_nobody_could_answer_is_refused_saying_which(number: str, why: str) -> None:
    with pytest.raises(DeclarationRefused, match=why):
        dial_policy.destination_of(number)


def test_every_calling_code_is_one_two_or_three_digits_and_the_longest_wins() -> None:
    assert all(re.fullmatch(r"\d{1,3}", code) for code in country_codes.CALLING_CODES)
    assert dial_policy.destination_of("+12423000000") == "+12423000000"

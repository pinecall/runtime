"""Pending sign-ups: a code kept as a hash, spent once, burned by six wrong, dead at fifteen."""

from __future__ import annotations

import pytest

from pinecall.auth.signups import ATTEMPTS, CODE_TTL_S, NotVerified, Pending, PendingSignups
from pinecall_testkit.clocks import Clock

pytestmark = pytest.mark.unit

ANA = "ana@tiendasur.uy"


def begun(signups: PendingSignups) -> str:
    """A sign-up asked for, and its code."""
    _, code = signups.begin(ANA, "tienda-sur", "Tienda Sur", "Ana", "argon2-hash", None)
    return code


def a_wrong_one(code: str) -> str:
    return "000000" if code != "000000" else "111111"


def test_the_code_is_six_digits_and_the_row_holds_only_its_hash() -> None:
    signups = PendingSignups()
    pending, code = signups.begin(ANA, "tienda-sur", None, "Ana", "argon2-hash", None)
    assert len(code) == 6 and code.isdigit()
    assert code.encode() not in pending.code_hash and code not in repr(pending)


def test_the_right_code_takes_the_sign_up_out_once() -> None:
    signups = PendingSignups()
    code = begun(signups)
    taken = signups.verify(ANA, code)
    assert isinstance(taken, Pending) and taken.slug == "tienda-sur"
    assert signups.verify(ANA, code) == NotVerified("wrong")


def test_six_wrong_codes_burn_it() -> None:
    signups = PendingSignups()
    code = begun(signups)
    said = [signups.verify(ANA, a_wrong_one(code)) for _ in range(ATTEMPTS)]
    assert said[-1] == NotVerified("burned") and said[0] == NotVerified("wrong")
    assert signups.verify(ANA, code) == NotVerified("burned")


def test_a_code_dies_at_fifteen_minutes() -> None:
    clock = Clock()
    signups = PendingSignups(clock)
    code = begun(signups)
    clock.now += CODE_TTL_S
    assert signups.verify(ANA, code) == NotVerified("expired")


def test_a_renewed_code_replaces_the_first_and_keeps_what_was_asked() -> None:
    signups = PendingSignups()
    first = begun(signups)
    renewed = signups.renewed(ANA)
    assert renewed is not None
    pending, second = renewed
    assert (pending.slug, pending.person, pending.hashed) == ("tienda-sur", "Ana", "argon2-hash")
    if first != second:
        assert signups.verify(ANA, first) == NotVerified("wrong")
    assert isinstance(signups.verify(ANA, second), Pending)


def test_nothing_to_renew_for_an_address_nobody_signed_up_with() -> None:
    assert PendingSignups().renewed(ANA) is None

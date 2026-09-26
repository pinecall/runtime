"""A login code stands for a key's record for five minutes, once."""

import pytest

from pinecall.auth.codes import CODE_PREFIX, CODE_TTL_S, LoginCodes
from pinecall.auth.keys import KeyRecord
from tests.clocks import Clock

pytestmark = pytest.mark.unit

A_RECORD = KeyRecord(key_id="k_1", org="clinica", env="sandbox", subject="m_1", name="Berna")


def test_a_code_is_spent_once_for_the_record_that_minted_it() -> None:
    codes = LoginCodes(Clock())
    minted = codes.mint(A_RECORD)
    assert minted.code.startswith(CODE_PREFIX)
    assert codes.spend(minted.code) == A_RECORD
    assert codes.spend(minted.code) is None, "once"


def test_a_code_dies_on_its_own_and_a_stranger_is_none() -> None:
    clock = Clock()
    codes = LoginCodes(clock)
    minted = codes.mint(A_RECORD)
    assert minted.expires_at == clock.now + CODE_TTL_S
    clock.now += CODE_TTL_S
    assert codes.spend(minted.code) is None
    assert codes.spend("lc_nobody_minted_this") is None


def test_two_codes_are_never_the_same_word() -> None:
    codes = LoginCodes()
    assert codes.mint(A_RECORD).code != codes.mint(A_RECORD).code

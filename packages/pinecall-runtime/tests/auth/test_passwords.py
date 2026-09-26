"""A password is argon2id at rest: never the text, never a fast hash, verified without leaking."""

import pytest

from pinecall.auth.passwords import hash_password, matches
from pinecall.settings import Settings
from pinecall.types import DeclarationRefused

pytestmark = pytest.mark.unit

A_PASSWORD = "correct horse battery staple"

# What the box says is short enough, handed in by the door. There is no number in the module: the
# floor is the operator's, and this suite asks the settings for it exactly as a door does.
A_FLOOR = Settings(world="production").min_password


async def test_the_hash_is_argon2id_carries_its_salt_and_never_the_password() -> None:
    kept = await hash_password(A_PASSWORD, A_FLOOR)
    assert kept.startswith("$argon2id$")
    assert A_PASSWORD not in kept
    assert await hash_password(A_PASSWORD, A_FLOOR) != kept, "a salt of its own each time"


async def test_the_right_password_matches_and_anything_else_does_not() -> None:
    kept = await hash_password(A_PASSWORD, A_FLOOR)
    assert await matches(A_PASSWORD, kept)
    assert not await matches(A_PASSWORD + "!", kept)
    assert not await matches("", kept)


async def test_nobodys_hash_matches_no_password_and_still_costs_a_verification() -> None:
    """An address nobody has is checked against a hash of nobody's, so the clock says nothing."""
    assert not await matches(A_PASSWORD, None)
    assert not await matches("", None)


async def test_a_hash_that_is_not_one_is_a_mismatch_and_never_an_error() -> None:
    assert not await matches(A_PASSWORD, "not-a-hash")
    assert not await matches(A_PASSWORD, "")


async def test_a_password_under_the_floor_is_refused_before_it_is_hashed() -> None:
    with pytest.raises(DeclarationRefused, match=f"at least {A_FLOOR}"):
        await hash_password("a" * (A_FLOOR - 1), A_FLOOR)
    assert await hash_password("a" * A_FLOOR, A_FLOOR)


async def test_the_floor_is_the_boxs_and_zero_is_no_rule_at_all() -> None:
    """Whoever runs the box decides, including deciding not to: `PINECALL_MIN_PASSWORD=0`."""
    assert await hash_password("a", 1)
    assert await hash_password("", 0), "nothing is a password where the operator asked for no rule"
    with pytest.raises(DeclarationRefused, match="at least 20"):
        await hash_password(A_PASSWORD[:19], 20)

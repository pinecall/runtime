"""A password is argon2id at rest: never the text, never a fast hash, verified without leaking."""

import pytest

from pinecall.auth.passwords import SHORTEST_PASSWORD, hashed, matches
from pinecall.types import DeclarationRefused

pytestmark = pytest.mark.unit

A_PASSWORD = "correct horse battery staple"


def test_the_hash_is_argon2id_carries_its_salt_and_never_the_password() -> None:
    kept = hashed(A_PASSWORD)
    assert kept.startswith("$argon2id$")
    assert A_PASSWORD not in kept
    assert hashed(A_PASSWORD) != kept, "a salt of its own each time"


def test_the_right_password_matches_and_anything_else_does_not() -> None:
    kept = hashed(A_PASSWORD)
    assert matches(A_PASSWORD, kept)
    assert not matches(A_PASSWORD + "!", kept)
    assert not matches("", kept)


def test_a_hash_that_is_not_one_is_a_mismatch_and_never_an_error() -> None:
    assert not matches(A_PASSWORD, "not-a-hash")
    assert not matches(A_PASSWORD, "")


def test_a_short_password_is_refused_before_it_is_hashed() -> None:
    with pytest.raises(DeclarationRefused, match=f"at least {SHORTEST_PASSWORD}"):
        hashed("a" * (SHORTEST_PASSWORD - 1))
    assert hashed("a" * SHORTEST_PASSWORD)

"""A one-use word: spent once, dead on its own after its time, and never growing with nobody's."""

import pytest

from pinecall.auth.one_use import OneUse
from pinecall_testkit.clocks import Clock

pytestmark = pytest.mark.unit


def test_a_word_is_spent_once_and_carries_its_prefix() -> None:
    words: OneUse[str] = OneUse("w_", 60.0, Clock())
    word, expires_at = words.mint("what it stands for")
    assert word.startswith("w_") and len(word) > 30 and expires_at == 1_060.0
    assert words.read(word) is not None
    assert words.spend(word) == "what it stands for"
    assert words.spend(word) is None and words.read(word) is None
    assert words.spend("w_nobody_minted_this") is None


def test_a_word_dies_on_its_own_when_its_time_is_up() -> None:
    clock = Clock()
    words: OneUse[int] = OneUse("w_", 60.0, clock)
    word, _ = words.mint(1)
    clock.now = 1_059.9
    assert words.read(word) is not None
    clock.now = 1_060.0
    assert words.read(word) is None and words.spend(word) is None


def test_a_word_minted_apart_from_its_value_keeps_the_moment_it_was_given() -> None:
    """A value that carries its own word is built first, then kept: the OpenID handshake."""
    words: OneUse[dict[str, str]] = OneUse("st_", 600.0, Clock())
    word, expires_at = words.a_word(), words.expires_from_now()
    words.keep(word, {"state": word}, expires_at)
    minted = words.read(word)
    assert minted is not None and (minted.value["state"], minted.expires_at) == (word, expires_at)

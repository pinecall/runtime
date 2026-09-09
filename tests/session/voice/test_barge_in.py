"""What cuts the agent off: the word count is livekit's, the stoplist is ours."""

from __future__ import annotations

import pytest

from pinecall.session.voice.barge_in import MIN_WORDS, is_a_backchannel, words_of

pytestmark = pytest.mark.unit


def test_two_words_is_the_number_livekit_is_handed() -> None:
    assert MIN_WORDS == 2


def test_words_are_counted_as_a_person_counts_them() -> None:
    assert words_of("Sí, sí... ¡el martes!") == ["sí", "sí", "el", "martes"]
    assert words_of("l'après-midi") == ["l'après", "midi"]
    assert words_of("") == []


@pytest.mark.parametrize(
    "said", ["sí", "sí sí", "ajá, claro", "Ok, dale.", "mm hmm", "vale vale vale"]
)
def test_agreement_alone_is_a_backchannel(said: str) -> None:
    assert is_a_backchannel(said) is True


@pytest.mark.parametrize(
    "said", ["no", "sí pero el martes no", "espere", "", "   ", "ok, cancele todo"]
)
def test_anything_that_takes_the_floor_is_not(said: str) -> None:
    assert is_a_backchannel(said) is False

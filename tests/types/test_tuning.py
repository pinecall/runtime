"""The tuning and the lexicon as shapes: a blank knob, a two-verb opening, a blank word refused."""

import re
from typing import Any

import pytest

from pinecall.types import BLANK, DeclarationRefused, Greeting, Lexicon, Tuning, Versions

pytestmark = pytest.mark.unit


def test_nothing_set_is_every_knob_none_the_bases_with_the_rest() -> None:
    """Absent is None for every knob: an empty `bases` is a decision, never "nobody set it"."""
    nothing = Tuning()
    assert (nothing.voice, nothing.llm, nothing.greeting, nothing.memory) == (None,) * 4
    assert nothing.knowledge is None and nothing.bases is None
    assert Tuning(bases=()).bases == ()


@pytest.mark.parametrize("knob", ["voice", "tts", "tts_model", "stt", "llm", "knowledge"])
def test_a_blank_named_knob_is_refused_in_the_sentence_that_says_why(knob: str) -> None:
    """convo ms-14: an empty voice reached the vendor and a line of calls went out silent."""
    blank: dict[str, Any] = {knob: "   "}
    with pytest.raises(DeclarationRefused, match=re.escape(BLANK.format(field=knob))):
        Tuning(**blank)


def test_an_opening_is_one_verb_here_as_it_is_on_the_class() -> None:
    with pytest.raises(DeclarationRefused, match="pick one"):
        Tuning(greeting=Greeting(say="Buenas.", reply="saluda y preséntate"))


def test_a_lexicon_refuses_a_blank_word_and_a_blank_spoken_form() -> None:
    with pytest.raises(DeclarationRefused, match="the lexicon"):
        Lexicon(said={"GSA": ""})
    with pytest.raises(DeclarationRefused, match="the lexicon"):
        Lexicon(heard=("Maravilla", " "))


def test_versions_none_is_a_corner_that_had_set_nothing() -> None:
    assert Versions() == Versions(config=None, lexicon=None)

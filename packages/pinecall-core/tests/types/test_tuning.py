"""The tuning and the lexicon as shapes: a blank knob, a two-verb opening, a blank word refused."""

import re
from typing import Any

import pytest

from pinecall.types import BLANK, DeclarationRefused, Greeting, Lexicon, Tuning, Versions
from pinecall.types.agent import Turn

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
        Lexicon(heard=("Clínica Norte", " "))


def test_versions_none_is_a_corner_that_had_set_nothing() -> None:
    assert Versions() == Versions(config=None, lexicon=None)


# Deepgram refuses a socket whose eager bar sits above its real one, and a refused socket is a call
# with no ears at all — so the two are held to their order where the class is declared, not on the
# first call of the day.
def test_a_turn_that_would_guess_later_than_it_decides_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="cannot sit above"):
        Turn(eot_threshold=0.7, eager_eot_threshold=0.9)


def test_the_eager_bar_may_sit_on_the_other_one() -> None:
    """Equal is Deepgram's own ceiling for it, not an error: guessing exactly when it is sure."""
    assert Turn(eot_threshold=0.85, eager_eot_threshold=0.85).eager_eot_threshold == 0.85
    assert Turn(eager_eot_threshold=0.4).eot_threshold is None


# A voice call's ceiling is no limit or a minute to an hour: shorter could not say goodbye, and an
# hour is the most the org chose to let a tenant ask for.
@pytest.mark.parametrize("seconds", [0, 60, 600, 3600])
def test_a_voice_calls_limit_is_none_or_a_minute_to_an_hour(seconds: int) -> None:
    assert Tuning(max_duration_s=seconds).max_duration_s == seconds


@pytest.mark.parametrize("seconds", [30, 59, 3601, -1])
def test_a_limit_outside_it_is_refused_in_words_that_say_the_range(seconds: int) -> None:
    with pytest.raises(DeclarationRefused, match="0 for no limit, or 60 to 3600 seconds"):
        Tuning(max_duration_s=seconds)

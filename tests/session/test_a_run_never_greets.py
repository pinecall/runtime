"""A call a run opened has no opening, on either channel: the state IS the conversation so far."""

import pytest

from pinecall.session.greeting import the_greeting_for
from pinecall.types import Greeting

pytestmark = pytest.mark.unit

THE_WORDS = Greeting(say="Clínica Norte, buenos días.")
A_RUN = "run_327da0835234"
NOBODY_RAN_IT = None


def test_a_call_a_person_opened_is_greeted_the_way_the_class_declared() -> None:
    assert the_greeting_for(THE_WORDS, NOBODY_RAN_IT) is THE_WORDS


# 2026-09-11: eight of eleven spoken goldens ended with a `turn.agent` and not one `turn.user`.
# The agent said its opening into a line where the caller was already saying their only sentence,
# the AEC warmup had interruptions disabled for three seconds, and the sentence was gone. The
# written suite never saw it because api/evals/golden_call.py had dropped the greeting since the
# day it landed — the two channels disagreed, and only one of them was right.
def test_a_call_a_run_opened_is_not_greeted_at_all() -> None:
    """It is mid-conversation by construction: an opening on top of it is a second answer."""
    assert the_greeting_for(THE_WORDS, A_RUN) is None


def test_an_improvised_opening_is_dropped_for_a_run_too() -> None:
    """`greeting = { reply: … }` costs a model call; a golden must not pay for one nobody reads."""
    assert the_greeting_for(Greeting(reply="saluda y preséntate"), A_RUN) is None


def test_a_class_that_declares_no_greeting_is_unchanged_by_any_of_this() -> None:
    assert the_greeting_for(None, A_RUN) is None
    assert the_greeting_for(None, NOBODY_RAN_IT) is None

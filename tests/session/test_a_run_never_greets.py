"""A call a run opened has no opening, on either channel: the state IS the conversation so far."""

import pytest

from pinecall.session.greeting import the_greeting_for
from pinecall.types import Greeting
from pinecall.types.dispatch import AN_EVAL_CALLER

pytestmark = pytest.mark.unit

THE_WORDS = Greeting(say="Clínica Norte, buenos días.")
A_RUNS_CALLER = f"{AN_EVAL_CALLER}327da0835234"
A_PERSON = "web_9f1c40aa77b1"


def test_a_real_caller_is_greeted_the_way_the_class_declared() -> None:
    assert the_greeting_for(THE_WORDS, A_PERSON) is THE_WORDS


def test_a_caller_nobody_identified_is_greeted_too() -> None:
    """A phone call with no number is still a person who just heard the line pick up."""
    assert the_greeting_for(THE_WORDS, None) is THE_WORDS


# 2026-09-11: eight of eleven spoken goldens ended with a `turn.agent` and not one `turn.user`.
# The agent said its opening into a line where the caller was already saying their only sentence,
# the AEC warmup had interruptions disabled for three seconds, and the sentence was gone. The
# written suite never saw it because api/evals/conversation.py had dropped the greeting since the
# day it landed — the two channels disagreed, and only one of them was right.
def test_a_call_a_run_opened_is_not_greeted_at_all() -> None:
    """It is mid-conversation by construction: an opening on top of it is a second answer."""
    assert the_greeting_for(THE_WORDS, A_RUNS_CALLER) is None


def test_an_improvised_opening_is_dropped_for_a_run_too() -> None:
    """`greeting = { reply: … }` costs a model call; a golden must not pay for one nobody reads."""
    assert the_greeting_for(Greeting(reply="saluda y preséntate"), A_RUNS_CALLER) is None


def test_a_class_that_declares_no_greeting_is_unchanged_by_any_of_this() -> None:
    assert the_greeting_for(None, A_RUNS_CALLER) is None
    assert the_greeting_for(None, A_PERSON) is None

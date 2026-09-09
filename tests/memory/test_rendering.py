"""What the memory marker becomes: one dash per fact, and nothing the view did not write."""

import pytest

from pinecall.memory import facts_as_text
from tests.memory.facts import a_fact

pytestmark = pytest.mark.unit


def test_each_fact_is_one_dashed_line_in_the_order_given() -> None:
    facts = [
        a_fact("1", "prefiere turnos por la mañana"),
        a_fact("2", "es alérgico a la penicilina"),
    ]
    assert facts_as_text(facts) == "- prefiere turnos por la mañana\n- es alérgico a la penicilina"


def test_a_fact_with_a_newline_in_it_is_still_one_line() -> None:
    assert facts_as_text([a_fact("1", "vive en\n  Montevideo")]) == "- vive en Montevideo"


def test_nothing_remembered_is_an_empty_string_and_not_a_dash() -> None:
    assert facts_as_text([]) == ""
    assert facts_as_text([a_fact("1", "   ")]) == ""

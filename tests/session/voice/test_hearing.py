"""What the ears are told to expect: the declaration first, then the names the state is holding."""

from __future__ import annotations

import pytest

from pinecall.session.voice import hearing
from pinecall.types import AgentConfig
from tests.session.voice.silence import SilentEars

pytestmark = pytest.mark.unit

CLARA = AgentConfig(
    slug="clinica-norte",
    channels=frozenset({"phone"}),
    hears=("Clínica Norte", "doctora Vidal"),
)


def test_an_agent_that_declared_nothing_and_holds_nothing_asks_for_nothing() -> None:
    assert hearing.words(AgentConfig(slug="clinica-norte")) == []


def test_the_declared_words_come_first_and_the_state_adds_the_names_it_is_holding() -> None:
    """This is the half a static list cannot have: a clinic cannot list its patients, but the
    class knows who it is talking to the moment a tool identified them."""
    state = {"patient": {"name": "Ana García", "phone": "600000001"}, "day": "martes"}
    assert hearing.words(CLARA, state) == ["Clínica Norte", "doctora Vidal", "Ana García", "martes"]


def test_a_phone_number_is_not_a_word_anybody_pronounces() -> None:
    assert hearing.words(AgentConfig(slug="c"), {"phone": "600000001"}) == []


def test_a_paragraph_of_notes_is_not_a_keyterm() -> None:
    """A keyterm biases the model towards a name; a sentence only dilutes every other one."""
    notes = {"notes": "el paciente pregunta si puede cambiar la cita del martes por la tarde"}
    assert hearing.words(AgentConfig(slug="c"), notes) == []


def test_a_name_the_agent_declared_and_the_state_also_holds_is_asked_for_once() -> None:
    heard = hearing.words(CLARA, {"doctor": "doctora Vidal"})
    assert heard == ["Clínica Norte", "doctora Vidal"]


def test_the_list_stops_where_the_vendors_do() -> None:
    crowded = {f"seat_{number}": f"paciente {number}" for number in range(hearing.MOST_TERMS + 20)}
    assert len(hearing.words(CLARA, crowded)) == hearing.MOST_TERMS


def test_only_ears_that_advertise_the_door_are_ever_handed_keyterms() -> None:
    assert hearing.takes_keyterms(SilentEars(keyterms=True)) is True
    assert hearing.takes_keyterms(SilentEars()) is False
    assert hearing.takes_keyterms(None) is False

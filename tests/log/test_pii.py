"""Masking on the way in: the names the agent declared, what they held, and what is untouched."""

import pytest

from pinecall.log.pii import MASK, Masker
from pinecall.types.agent import AgentConfig
from pinecall.types.tool import ToolSpec

pytestmark = pytest.mark.unit

BOOK = ToolSpec(
    name="book_slot",
    description="Book a slot for a patient",
    parameters={
        "type": "object",
        "properties": {"patient": {"type": "string"}, "at": {"type": "string"}},
    },
    side_effect="irreversible",
    confirm="I book {at} for you, yes?",
    pii=frozenset({"patient"}),
)

CONFIG = AgentConfig(
    slug="clinica-norte",
    state_fields={"patient": "pii", "slots": "public", "stage": "tenant"},
    tools=(BOOK,),
)

# What the app's state carries the first time it changes: this is where the masker learns.
A_PATIENT = {"id": "P-2231", "name": "Marta Ruiz", "phone": "+34600123456"}


def a_masker() -> Masker:
    """A masker that has already seen the patient, the way a call would have shown it one."""
    masker = Masker(CONFIG)
    masker.mask("state.changed", {"state": {"patient": dict(A_PATIENT)}, "changed": ["patient"]})
    return masker


def test_state_changed_is_where_it_learns_and_is_left_whole() -> None:
    """The tenant's own console reads app_state; the tenant projection masks it at the sink."""
    masker = Masker(CONFIG)
    entry = {"state": {"patient": dict(A_PATIENT), "stage": "greeting"}, "changed": ["patient"]}
    assert masker.mask("state.changed", entry) == entry
    assert set(masker.learned) == {"P-2231", "Marta Ruiz", "+34600123456"}


def test_a_tool_argument_the_agent_declared_pii_is_masked_before_it_is_written() -> None:
    masked = a_masker().mask(
        "tool.call",
        {
            "call_id": "toolu_02",
            "name": "book_slot",
            "arguments": {"patient": "P-2231", "at": "2026-08-13T09:30"},
        },
    )
    assert masked["arguments"] == {"patient": MASK, "at": "2026-08-13T09:30"}
    assert masked["name"] == "book_slot", "the tool's own name is not one of its arguments"


def test_a_tool_that_declared_nothing_keeps_its_arguments() -> None:
    masked = a_masker().mask(
        "tool.call", {"call_id": "t1", "name": "find_slots", "arguments": {"doctor": "vidal"}}
    )
    assert masked["arguments"] == {"doctor": "vidal"}


def test_a_learned_value_is_masked_wherever_it_turns_up_later() -> None:
    """This is the half a name-only masker misses: the same value, under somebody else's key."""
    masked = a_masker().mask(
        "custom", {"name": "crm.push", "data": {"note": "llamar a Marta Ruiz al +34600123456"}}
    )
    assert masked["data"]["note"] == f"llamar a {MASK} al {MASK}"


def test_the_longest_learned_value_masks_first() -> None:
    """Marta before Marta Ruiz would leave the surname standing next to a mask."""
    masker = Masker(CONFIG)
    masker.learn(["Marta", "Marta Ruiz"])
    masked = masker.mask("custom", {"name": "n", "data": {"text": "Marta Ruiz vino"}})
    assert masked["data"]["text"] == f"{MASK} vino"


@pytest.mark.parametrize(
    "type", ["turn.user", "turn.agent", "metrics.llm", "metrics.stt", "user.transcript"]
)
def test_the_words_and_the_numbers_are_never_touched(type: str) -> None:
    """The mistake the references made: a masked transcript is not a transcript."""
    said = {"text": "soy Marta Ruiz, mi telefono es +34600123456", "patient": "P-2231"}
    assert a_masker().mask(type, dict(said)) == said


def test_a_masker_with_no_config_masks_nothing() -> None:
    """An agent that declared nothing has nothing personal by declaration, and that is the rule."""
    entry = {"arguments": {"patient": "P-2231"}}
    assert Masker().mask("tool.call", dict(entry)) == entry


def test_a_value_too_short_to_be_a_name_is_never_learned() -> None:
    """A one-letter value would mask the alphabet; the floor is what keeps the log readable."""
    masker = Masker(CONFIG)
    masker.mask("state.changed", {"state": {"patient": {"initial": "M"}}, "changed": ["patient"]})
    assert masker.learned == ()
    assert masker.mask("custom", {"data": {"text": "Marta"}})["data"]["text"] == "Marta"

"""ToolSpec: the rules the platform holds a tool to before its method ever runs."""

from dataclasses import fields
from typing import Any

import pytest

from pinecall.types import DeclarationRefused, ToolSpec
from pinecall.types.tool import SIDE_EFFECTS

pytestmark = pytest.mark.unit

A_DAY_AND_A_TIME: dict[str, Any] = {
    "type": "object",
    "properties": {"day": {"type": "string"}, "time": {"type": "string"}},
    "required": ["day", "time"],
}


def a_tool(**declared: Any) -> ToolSpec:
    """A well-formed read tool, with whatever the test changes laid on top."""
    given: dict[str, Any] = {
        "name": "free_slots",
        "description": "Free slots of a day.",
        "parameters": A_DAY_AND_A_TIME,
        **declared,
    }
    return ToolSpec(**given)


def test_an_irreversible_tool_needs_a_confirm_template() -> None:
    with pytest.raises(DeclarationRefused, match="confirm template"):
        a_tool(name="book_slot", side_effect="irreversible")
    booking = a_tool(
        name="book_slot",
        side_effect="irreversible",
        confirm="Le reservo el {day} a las {time}. ¿Lo confirmo?",
    )
    assert booking.requires_confirmation


def test_a_read_tool_needs_no_yes_but_a_template_gates_any_tool() -> None:
    assert not a_tool().requires_confirmation
    assert a_tool(side_effect="write", confirm="¿Lo cambio?").requires_confirmation


def test_a_pii_field_names_a_parameter_the_tool_has() -> None:
    assert a_tool(pii=frozenset({"day"})).pii == {"day"}
    with pytest.raises(DeclarationRefused, match=r"unknown: \['phone'\]"):
        a_tool(pii=frozenset({"phone"}))


def test_a_tool_name_is_one_word_a_model_can_call() -> None:
    assert a_tool(name="findPatient").name == "findPatient"
    for name in ("", "find patient", "find.patient", "1st_slot"):
        with pytest.raises(DeclarationRefused, match="one word"):
            a_tool(name=name)


def test_a_tool_without_a_description_is_one_no_model_can_choose() -> None:
    with pytest.raises(DeclarationRefused, match="description"):
        a_tool(description="   ")


def test_parameters_are_a_json_schema_object() -> None:
    with pytest.raises(DeclarationRefused, match="type object"):
        a_tool(parameters={"type": "array"})
    assert a_tool(parameters={"type": "object"}).parameter_names == frozenset()
    assert a_tool().parameter_names == {"day", "time"}


def test_a_preview_shows_at_least_one_item_and_a_timeout_is_positive() -> None:
    assert a_tool(preview=2).preview == 2
    with pytest.raises(DeclarationRefused, match="at least one"):
        a_tool(preview=0)
    with pytest.raises(DeclarationRefused, match="positive"):
        a_tool(timeout_s=0)


def test_side_effects_are_a_closed_set() -> None:
    assert SIDE_EFFECTS == {"read", "write", "irreversible"}
    with pytest.raises(DeclarationRefused, match="side_effect"):
        a_tool(side_effect="destructive")


def test_when_is_the_apps_business_and_has_no_field_here() -> None:
    assert "when" not in {declared.name for declared in fields(ToolSpec)}

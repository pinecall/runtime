"""The markers a view writes: found by line, read by payload, and replaced by their fills."""

import pytest

from pinecall.types import DeclarationRefused, Marker, filled, markers_in

pytestmark = pytest.mark.unit

MEMORY = '<!-- memory: {"kinds":["preference","health"],"limit":6} -->'
RETRIEVED = '<!-- retrieved: {"k":4,"min_score":0.02} -->'
KNOWLEDGE = "<!-- knowledge: ./knowledge/clinica.md -->"

A_VIEW = f"""## What you remember about the caller

{MEMORY}

## Rules

- Never invent an appointment."""


def test_a_marker_on_its_own_line_is_found_with_its_name_its_payload_and_its_line() -> None:
    assert markers_in(A_VIEW) == (
        Marker(name="memory", payload='{"kinds":["preference","health"],"limit":6}', line=MEMORY),
    )


def test_the_three_markers_are_found_in_the_order_written_and_prose_is_not() -> None:
    text = (
        f"{KNOWLEDGE}\nSome prose.\n<!-- a comment that is not a marker -->\n{RETRIEVED}\n{MEMORY}"
    )
    assert [marker.name for marker in markers_in(text)] == ["knowledge", "retrieved", "memory"]


def test_an_indented_marker_is_still_a_marker_and_keeps_its_line_whole() -> None:
    line = f"    {MEMORY}"
    (marker,) = markers_in(f"Rules:\n{line}")
    assert marker.line == line
    assert marker.name == "memory"


def test_a_knowledge_payload_is_the_bare_path() -> None:
    (marker,) = markers_in(KNOWLEDGE)
    assert marker.payload == "./knowledge/clinica.md"


def test_an_empty_payload_asks_for_the_defaults() -> None:
    (marker,) = markers_in("<!-- memory: -->")
    assert (marker.ask.kinds, marker.ask.limit, marker.ask.min_score) == ((), None, None)


def test_kinds_limit_and_min_score_are_read_off_the_payload() -> None:
    (memory,) = markers_in(MEMORY)
    (retrieved,) = markers_in(RETRIEVED)
    assert memory.ask.kinds == ("preference", "health")
    assert memory.ask.limit == 6
    assert (retrieved.ask.limit, retrieved.ask.min_score) == (4, 0.02)


def test_k_is_limit_and_min_score_is_read_in_either_case() -> None:
    """TypeScript wrote minScore before ms-9 and writes min_score now; both are the same ask."""
    (camel,) = markers_in('<!-- retrieved: {"k":3,"minScore":0.5} -->')
    (snake,) = markers_in('<!-- retrieved: {"limit":3,"min_score":0.5} -->')
    assert camel.ask == snake.ask


def test_a_render_prop_id_travels_in_the_payload_and_is_not_read() -> None:
    (marker,) = markers_in('<!-- memory: {"limit":2,"fill":"f_1"} -->')
    assert marker.ask.limit == 2
    assert not hasattr(marker.ask, "fill")


def test_a_payload_that_is_not_a_json_object_is_refused_naming_the_marker() -> None:
    (garbage,) = markers_in("<!-- memory: kinds=preference -->")
    with pytest.raises(DeclarationRefused, match="a memory marker carries JSON"):
        _ = garbage.ask
    (a_list,) = markers_in('<!-- retrieved: ["k"] -->')
    with pytest.raises(DeclarationRefused, match="a retrieved marker carries a JSON object"):
        _ = a_list.ask


def test_a_fill_takes_the_place_of_the_marker_line() -> None:
    facts = "- Prefers mornings.\n- Allergic to penicillin."
    assert filled(A_VIEW, {MEMORY: facts}) == A_VIEW.replace(MEMORY, facts)


def test_an_empty_fill_removes_the_line_and_one_blank_line_after_it() -> None:
    text = f"The caller is Ana.\n\n{MEMORY}\n\n- Never invent an appointment."
    assert filled(text, {MEMORY: ""}) == "The caller is Ana.\n\n- Never invent an appointment."


def test_an_empty_fill_takes_the_heading_the_view_wrote_over_it_when_nothing_else_is_under_it() -> (
    None
):
    """A heading over silence reads as a fact: "you remember" nothing is not what it says."""
    assert filled(A_VIEW, {MEMORY: ""}) == "## Rules\n\n- Never invent an appointment."
    assert filled(f"Hola.\n\n## You remember\n{MEMORY}", {}) == "Hola."


def test_a_heading_over_a_filled_marker_stays() -> None:
    assert filled(A_VIEW, {MEMORY: "- Prefers mornings."}).startswith(
        "## What you remember about the caller\n\n- Prefers mornings.\n\n## Rules"
    )


def test_a_heading_with_something_else_under_it_stays_when_a_marker_under_it_is_emptied() -> None:
    prose = f"## Relevant\n\n{RETRIEVED}\n\nAlways quote the price list."
    assert filled(prose, {}) == "## Relevant\n\nAlways quote the price list."
    two = f"## Relevant\n\n{RETRIEVED}\n{MEMORY}"
    assert filled(two, {MEMORY: "- Prefers mornings."}) == "## Relevant\n\n- Prefers mornings."


def test_a_marker_the_fills_leave_out_is_filled_with_nothing() -> None:
    """The model never reads a comment: an unanswered marker leaves as an empty one does."""
    assert filled(A_VIEW, {}) == filled(A_VIEW, {MEMORY: ""})


def test_a_marker_on_the_last_line_leaves_no_trailing_newline_behind() -> None:
    assert filled(f"Rules.\n{MEMORY}", {}) == "Rules."


def test_two_markers_are_each_replaced_by_their_own_fill() -> None:
    text = f"{KNOWLEDGE}\n\n{RETRIEVED}"
    assert filled(text, {KNOWLEDGE: "The clinic opens at nine.", RETRIEVED: "### Tarifas"}) == (
        "The clinic opens at nine.\n\n### Tarifas"
    )


def test_a_block_with_no_marker_comes_back_byte_for_byte() -> None:
    prose = "You are Clara.\n\n<!-- a comment, kept -->\n\nNever invent an appointment."
    assert filled(prose, {MEMORY: "anything"}) == prose

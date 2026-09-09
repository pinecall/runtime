"""The remember step in ring 0: what the model is shown, how strictly it is read, and the policy."""

import pytest

from pinecall.memory.extraction import Op, allowed, extracted, parsed
from pinecall.memory.protocol import Spoken
from pinecall.types import MemoryPolicy
from tests.memory.facts import a_fact
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

CLEAN = (
    '[{"op": "add", "text": "Es alérgico a la penicilina", "category": "health"},'
    ' {"op": "update", "of": "k1", "text": "Prefiere la tarde", "category": "preference"},'
    ' {"op": "invalidate", "of": "k2"}]'
)

FENCED = f"```json\n{CLEAN}\n```"

GARBAGE = "Claro, acá van los cambios: el paciente prefiere la tarde."

THE_POLICY = MemoryPolicy(remember=("preference", "health"), forget=("religion",))


# ── parsing ───────────────────────────────────────────────────────────────────


def test_a_clean_array_is_read_op_by_op() -> None:
    assert parsed(CLEAN) == [
        Op(op="add", text="Es alérgico a la penicilina", category="health"),
        Op(op="update", text="Prefiere la tarde", category="preference", of="k1"),
        Op(op="invalidate", of="k2"),
    ]


def test_a_fence_around_the_array_is_forgiven() -> None:
    assert parsed(FENCED) == parsed(CLEAN)


def test_garbage_is_zero_ops_and_never_an_exception() -> None:
    assert parsed(GARBAGE) == []
    assert parsed("") == []
    assert parsed('{"op": "add", "text": "an object, not an array"}') == []


def test_an_element_that_is_not_an_op_this_package_knows_is_dropped_alone() -> None:
    answer = (
        '[{"op": "delete", "of": "k1"}, {"op": "add", "text": "  "},'
        ' {"op": "update", "text": "sin id"}, "una cadena",'
        ' {"op": "add", "text": "  Vive en   Pocitos ", "category": " address "}]'
    )
    assert parsed(answer) == [Op(op="add", text="Vive en Pocitos", category="address")]


# ── the policy ────────────────────────────────────────────────────────────────


def test_a_forget_category_never_becomes_a_row_whatever_its_case() -> None:
    ops = [Op(op="add", text="Es católico", category="Religion")]
    assert allowed(ops, THE_POLICY, []) == []


def test_an_update_or_an_invalidate_of_a_fact_the_contact_does_not_have_is_dropped() -> None:
    known = [a_fact("k1")]
    ops = [Op(op="update", of="k9", text="x", category="preference"), Op(op="invalidate", of="k1")]
    assert allowed(ops, THE_POLICY, known) == [Op(op="invalidate", of="k1")]


def test_a_known_fact_is_replaced_at_most_once_per_call() -> None:
    known = [a_fact("k1")]
    ops = [
        Op(op="update", of="k1", text="first", category="preference"),
        Op(op="update", of="k1", text="second", category="preference"),
    ]
    assert allowed(ops, THE_POLICY, known) == [ops[0]]


# ── the request ───────────────────────────────────────────────────────────────


async def test_the_model_is_shown_the_categories_the_known_facts_and_the_turns() -> None:
    model = FakeLLM(Scripted(chunks=(CLEAN,)))
    known = [
        a_fact("k1", "Prefiere la mañana"),
        a_fact("k2", "Vive en Pocitos", category="address"),
    ]
    turns = [Spoken("user", "ahora prefiero la tarde"), Spoken("agent", "anotado")]
    ops = await extracted(model, known=known, turns=turns, policy=THE_POLICY, channel="phone")
    asked = model.asked[0]
    assert "preference, health" in asked.system
    assert "Never keep anything about: religion" in asked.system
    shown = asked.history[0].text_content or ""
    assert "- k1 · preference · Prefiere la mañana" in shown
    assert "- k2 · address · Vive en Pocitos" in shown
    assert "The call, on phone:\nuser: ahora prefiero la tarde\nagent: anotado" in shown
    assert [op.op for op in ops] == ["add", "update", "invalidate"]


async def test_with_nothing_known_the_model_is_told_so_and_no_forget_line_is_written() -> None:
    model = FakeLLM(Scripted(chunks=("[]",)))
    policy = MemoryPolicy(remember=("preference",))
    assert await extracted(model, known=[], turns=[], policy=policy, channel="web") == []
    assert "Never keep" not in model.asked[0].system
    assert (model.asked[0].history[0].text_content or "").startswith("Nothing is known")

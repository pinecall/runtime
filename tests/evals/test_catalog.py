"""Tests for the library of judges: every page parses, the panel, the gates, the facts."""

import dataclasses
from collections.abc import Iterator
from pathlib import Path

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.judging import A_NAME, JudgeSpec
from pinecall.evals import catalog
from pinecall.evals.case import Case, case_of
from pinecall.evals.catalog import Seated, Surroundings, facts_of, gated, library, panel_of
from tests.evals.conftest import THE_CLINIC, agent_line, caller_line, case_of_turns, confirmed

ON_BY_DEFAULT = {
    "consent",
    "disclosed",
    "ended-well",
    "expected-outcome",
    "grounded",
    "honoured-stop",
    "identified",
    "promises",
}


SLOT = JudgeSpec(name="offers-next-slot", question="Did the agent offer the next free slot?")


REHEARSED = JudgeSpec(name="names-the-doctor", question="Was the doctor named?", on="simulations")


A_PAGE = """---
name: polite
summary: The agent was polite.
answer: verdict
on: always
reads: facts, prompt
gate: none
default: off
version: 3
---

Was the agent polite throughout?
"""


@pytest.fixture
def written_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A library read from a folder of the test's, forgotten again after it."""
    monkeypatch.setattr(catalog, "LIBRARY", tmp_path)
    library.cache_clear()
    yield tmp_path
    library.cache_clear()


def plain() -> Case:
    return case_of_turns(caller_line("¿Hay hueco?"), agent_line("El viernes a las diez."))


def names(seated: list[Seated]) -> list[str]:
    return [judge.spec.name for judge in seated]


# ── the library ──


def test_every_page_of_the_library_parses_into_a_judge_named_as_its_file() -> None:
    judges = library()
    assert {path.stem for path in catalog.LIBRARY.glob("*.md")} == set(judges)
    for name, judge in judges.items():
        assert A_NAME.match(name)
        assert judge.spec.question.strip()
        assert judge.summary
        assert judge.version >= 1


def test_the_judges_on_by_default_are_the_ones_every_call_needs() -> None:
    on = {name for name, judge in library().items() if judge.on_by_default}
    assert on == ON_BY_DEFAULT
    assert {"relevance", "repetition", "sentiment"} <= set(library()) - on


def test_a_judge_that_classifies_names_its_choices() -> None:
    sentiment = library()["sentiment"].spec
    assert (sentiment.answer, sentiment.choices) == ("choice", ("positive", "neutral", "negative"))


def test_a_page_is_read_whole_into_its_spec(written_library: Path) -> None:
    (written_library / "polite.md").write_text(A_PAGE, encoding="utf-8")
    polite = library()["polite"]
    assert polite.spec.question == "Was the agent polite throughout?"
    assert (polite.spec.reads_facts, polite.spec.reads_prompt, polite.spec.reads_evidence) == (
        True,
        True,
        False,
    )
    assert (polite.summary, polite.version, polite.on_by_default, polite.gate) == (
        "The agent was polite.",
        3,
        False,
        "none",
    )


def test_a_page_with_no_front_matter_is_refused(written_library: Path) -> None:
    (written_library / "bare.md").write_text("Was the agent polite?\n", encoding="utf-8")
    with pytest.raises(DeclarationRefused, match="opens with its front matter"):
        library()


def test_a_page_with_a_gate_nobody_knows_is_refused(written_library: Path) -> None:
    page = A_PAGE.replace("gate: none", "gate: weekends")
    (written_library / "polite.md").write_text(page, encoding="utf-8")
    with pytest.raises(DeclarationRefused, match="gate is 'weekends'"):
        library()


# ── the panel ──


def test_a_real_call_meets_the_library_on_by_default_in_name_order_and_no_rehearsal() -> None:
    seated = panel_of(plain(), {}, ())
    assert names(seated) == sorted(ON_BY_DEFAULT - {"expected-outcome"})


def test_a_simulated_call_meets_the_judges_only_simulations_run() -> None:
    simulated = dataclasses.replace(plain(), simulated=True)
    assert "expected-outcome" in names(panel_of(simulated, {}, ()))
    assert REHEARSED.name in names(panel_of(simulated, {}, (REHEARSED,)))
    assert REHEARSED.name not in names(panel_of(plain(), {}, (REHEARSED,)))


def test_a_switch_turns_one_of_pinecalls_on_or_off() -> None:
    seated = names(panel_of(plain(), {"grounded": False, "sentiment": True}, ()))
    assert "grounded" not in seated
    assert "sentiment" in seated


def test_the_orgs_own_judges_follow_the_library_in_the_order_given() -> None:
    other = JudgeSpec(name="says-the-price", question="Was the price said?")
    seated = panel_of(plain(), {}, (SLOT, other))
    assert names(seated)[-2:] == [SLOT.name, other.name]
    assert (seated[-1].gate, seated[-1].version) == ("none", None)
    assert seated[0].version == library()[seated[0].spec.name].version


# ── the gates ──


def library_seat(name: str) -> Seated:
    judge = library()[name]
    return Seated(judge.spec, judge.gate, judge.version)


def test_consent_is_na_on_a_call_no_irreversible_tool_ran_on() -> None:
    assert gated(library_seat("consent"), plain()) == (
        "no tool this agent declares irreversible ran on this call"
    )
    assert gated(library_seat("consent"), case_of(confirmed(), THE_CLINIC)) is None


def test_identified_is_na_on_a_call_that_came_in() -> None:
    inbound = dataclasses.replace(plain(), direction="inbound")
    outbound = dataclasses.replace(plain(), direction="outbound")
    assert gated(library_seat("identified"), inbound) is not None
    assert gated(library_seat("identified"), outbound) is None


def test_expected_outcome_is_na_when_the_simulated_caller_expected_nothing() -> None:
    expecting = dataclasses.replace(plain(), persona_rule=("a price", ""))
    assert gated(library_seat("expected-outcome"), plain()) == (
        "the simulated caller expected nothing of the agent"
    )
    assert gated(library_seat("expected-outcome"), expecting) is None


def test_a_judge_with_no_gate_is_always_asked() -> None:
    assert gated(Seated(SLOT), plain()) is None


# ── the facts ──


def test_the_facts_name_the_org_the_direction_and_the_irreversible_tools_and_gate() -> None:
    lines = facts_of(case_of(confirmed(), THE_CLINIC), Surroundings(org="Clínica Norte"))
    assert lines.split("\n") == [
        "- organisation: Clínica Norte",
        "- direction: unknown",
        "- irreversible tools: book_appointment",
        "- #3 confirm.request book_appointment (caller)",
        "- #5 confirm.granted book_appointment (caller)",
        "- an opt-out was written on this call: no",
    ]


def test_the_facts_carry_the_opt_out_the_disclosure_the_ending_and_the_expectations() -> None:
    case = dataclasses.replace(
        plain(),
        direction="outbound",
        summary={"end_reason": "caller_hung_up", "ended_by": "caller"},
        persona_rule=("a price", "a call back"),
    )
    around = Surroundings(org="Clínica Norte", disclosure="Le llama un asistente.", opted_out=True)
    lines = facts_of(case, around).split("\n")
    assert lines[1] == "- direction: outbound"
    assert lines[2] == "- irreversible tools: none"
    assert lines[3:] == [
        "- an opt-out was written on this call: yes",
        '- the disclosure sentence outbound calls open with: "Le llama un asistente."',
        "- the call ended: caller_hung_up, by caller",
        "- the simulated caller expected:",
        "  - a price",
        "  - a call back",
    ]

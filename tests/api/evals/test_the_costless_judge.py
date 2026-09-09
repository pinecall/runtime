"""Consent judges every golden run, whatever `expect` names — and it is the column that goes red."""

from typing import Any

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.api.evals.conversation import Conversation
from pinecall.api.evals.scoring import Judging
from pinecall.evals.goldens import Expect, Golden
from pinecall.providers.declaration import a_tool
from pinecall.types import GATE_DEFERRED_ON, AgentConfig
from tests.api.evals.conftest import (
    AGENT,
    BEFORE_THE_YES,
    BOOK,
    NO_GATE,
    RUN,
    a_golden,
    entries_of,
    serving,
)
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

# The clinic as the judges see it when no app socket is in the way: the one declaration that says
# which of its tools takes a slot away from somebody else.
THE_CLINIC = AgentConfig(slug=AGENT, tools=(a_tool(BOOK),))


async def judged(one: Conversation) -> dict[str, Any]:
    """One conversation through the clinic's own judging, and the matrix that one cell makes."""
    judging = Judging(THE_CLINIC)
    await judging.judged(one)
    return judging.matrix


def scored(name: str, expect: Expect | None = None) -> Conversation:
    """One of the fixture logs as a finished golden run, ready for the door's own scorer."""
    return Conversation(
        golden=Golden(name=name, expect=expect or Expect()),
        model="declared",
        call=f"call_{name}",
        entries=entries_of(name),
    )


async def test_a_golden_that_expects_nothing_at_all_is_still_judged_by_consent(
    suite_http: httpx.AsyncClient, registry: Registry, llm: FakeLLM
) -> None:
    """`expect: {}` used to be scored by nobody at all; it is scored by the policy that is free."""
    await serving(registry)
    llm.script.append(Scripted(chunks=("Buenos días, soy Clara.",)))

    answered = await suite_http.post(
        RUN, json={"agent": AGENT, "goldens": [a_golden("asks nothing", ["hola"])]}
    )

    assert answered.status_code == httpx.codes.OK, answered.text
    matrix: dict[str, Any] = answered.json()["matrix"]
    assert matrix["metrics"] == ["consent"]
    scores = matrix["runs"][0]["scores"]
    assert [score["metric"] for score in scores] == ["consent"]
    assert scores[0]["passed"] is True
    assert "no irreversible tool ran in this call" in scores[0]["reason"]
    # The whole point of a policy: it decided from the log, and nobody was asked anything.
    assert matrix["judge_calls"] == 0


async def test_consent_leads_the_columns_a_golden_did_ask_for(
    suite_http: httpx.AsyncClient, registry: Registry, llm: FakeLLM
) -> None:
    """It sits beside `expect`, never instead of it: the declared judges all still run."""
    await serving(registry)
    llm.script.append(Scripted(chunks=("La consulta cuesta 45 euros.",)))
    golden = a_golden("prices", ["¿cuánto cuesta?"], expect={"says": ["45"], "not": ["gratis"]})

    answered = await suite_http.post(RUN, json={"agent": AGENT, "goldens": [golden]})

    assert answered.status_code == httpx.codes.OK, answered.text
    matrix: dict[str, Any] = answered.json()["matrix"]
    # The order is the schema's — `not` is declared before `says` — with consent at the head.
    assert matrix["metrics"] == ["consent", "silence", "says"]
    assert not matrix["failures"]
    assert matrix["judge_calls"] == 0


# A golden driven through the door today cannot mint a `confirm.granted`: the gate was deferred on
# 2026-09-06 (docs/decisions/confirm.md), so a live booking reads `ungated` and holds. The break
# this always-on column exists to catch is therefore pinned against the fixture log that does carry
# a gate — the same `_judges_for`, the same empty `expect`, the same matrix the door answers with.
async def test_the_booking_that_ran_before_the_yes_turns_the_consent_column_red() -> None:
    """A grant that arrived after the tool: the reason names both seqs, and nothing was asked."""
    matrix = await judged(scored(BEFORE_THE_YES))

    assert matrix["metrics"] == ["consent"]
    assert [failed["metric"] for failed in matrix["failures"]] == ["consent"]
    score = matrix["runs"][0]["scores"][0]
    assert score["passed"] is False
    assert score["reason"] == "book_appointment ran at seq 4, before its confirm.granted at seq 6"
    assert matrix["judge_calls"] == 0


async def test_a_booking_with_no_gate_at_all_holds_and_says_the_gate_is_deferred() -> None:
    """A green consent cell is read by its reason: this one is the deferral, not a caller's yes."""
    matrix = await judged(scored(NO_GATE))

    score = matrix["runs"][0]["scores"][0]
    assert score["passed"] is True
    assert GATE_DEFERRED_ON in score["reason"]


# What consent alone could not catch, and the reason `expect.not_tools` exists. This is
# the very log the cell above holds on: the gate is deferred, so the ORDER rule has nothing to
# read, and a golden that says "this call must never book" is ring 1's only witness until it lands.
async def test_a_golden_that_forbids_the_tool_goes_red_where_consent_can_only_hold() -> None:
    """Two columns over one log: consent green on the deferral, not_tools red on the seq."""
    forbidden = Expect(not_tools=["book_appointment"])

    matrix = await judged(scored(NO_GATE, forbidden))

    assert matrix["metrics"] == ["consent", "not_tools"]
    scores = {score["metric"]: score for score in matrix["runs"][0]["scores"]}
    assert scores["consent"]["passed"] is True
    assert scores["not_tools"]["passed"] is False
    assert scores["not_tools"]["reason"] == (
        "the golden forbids book_appointment, and this call ran book_appointment at seq 3"
    )
    assert [failed["metric"] for failed in matrix["failures"]] == ["not_tools"]
    # The mirror of `expect.tools` is a policy like the rest of them: nobody was asked anything.
    assert matrix["judge_calls"] == 0


async def test_a_golden_forbids_a_tool_the_call_never_touched_and_the_column_holds() -> None:
    matrix = await judged(scored(NO_GATE, Expect(not_tools=["transfer"])))

    assert not matrix["failures"]
    assert matrix["judge_calls"] == 0

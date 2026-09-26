"""What a golden expects: the tools it names and forbids, the words it wants, the fact it gets."""

from __future__ import annotations

from typing import Any

import pytest

from pinecall.evals import (
    Case,
    EveryPhraseWasSaidJudge,
    EveryToolRanJudge,
    NoForbiddenToolRanJudge,
    NothingWasSaidJudge,
    TheEventWasAnsweredJudge,
    build_case,
)
from pinecall_testkit.pinned_logs import BOOKING, a_log
from tests.evals.conversations import a_call, a_case_of, arrived, asked, ran, replied
from tests.evals.fakes import CountingJudge
from tests.evals.measuring import measured

pytestmark = pytest.mark.unit

HELLO = "Hola, ¿hay hueco el martes?"

LOOKED_UP = a_call("find_slots", {"day": "martes"}, {"slots": []})


def a_conversation(*said: str, called: bool = False) -> Case:
    """One question and the agent's answers to it, numbered the way the log numbers them."""
    turns = [asked(HELLO)]
    for number, text in enumerate(said, 1):
        turns.append(
            replied(text, seq=number * 2, calls=[LOOKED_UP] if called and number == 1 else [])
        )
    return a_case_of(*turns)


def with_an_event(name: str, data: dict[str, Any], *, at: int) -> Case:
    """A conversation the bridge would have built with one `event.received` in the middle of it."""
    return a_case_of(
        asked(HELLO),
        replied("Un momento.", seq=2),
        asked("¿Y ahora?", seq=at + 1),
        replied("Se liberó el de las 10:15.", seq=at + 2),
        events=[arrived(name, data, seq=at)],
    )


# ── the tools ───────────────────────────────────────────────────────────────────


async def test_a_conversation_that_called_every_named_tool_holds_without_a_judge() -> None:
    judge = CountingJudge()
    case = a_conversation("Le busco un hueco.", called=True)

    assert (await measured(EveryToolRanJudge(["find_slots"]), case, judge)).score == 1.0
    assert judge.prompts == []


async def test_a_tool_the_golden_named_and_nobody_called_is_named_in_the_reason() -> None:
    case = a_conversation("Le busco un hueco.", called=True)

    score = await measured(EveryToolRanJudge(["book_slot"]), case, CountingJudge())

    assert score.score == 0.0
    assert "book_slot" in score.reason
    assert "find_slots" in score.reason


async def test_a_conversation_that_called_nothing_says_so_rather_than_listing_nothing() -> None:
    score = await measured(
        EveryToolRanJudge(["find_slots"]), a_conversation("No sé."), CountingJudge()
    )

    assert "no tool at all" in score.reason


# ── the tool that must NOT have run ─────────────────────────────────────────────

# The break this judge exists for, over the log that has it: `book_appointment` ran at seq 3 and
# nothing asked anybody. Consent HOLDS on this same log — the gate is deferred (docs/decisions/
# confirm.md), so the rule reads `ungated` — which is exactly why a golden needs a second witness.


async def test_a_forbidden_tool_that_ran_is_caught_with_the_seq_the_log_gave_it() -> None:
    judge = CountingJudge()
    case = build_case(a_log("booking-with-no-gate"), tools=BOOKING)

    score = await measured(NoForbiddenToolRanJudge(["book_appointment"], case.gate), case, judge)

    assert score.score == 0.0
    assert "this call ran book_appointment at seq 3" in score.reason
    assert judge.prompts == []


async def test_a_call_that_kept_off_the_forbidden_tool_holds_and_asks_nobody() -> None:
    judge = CountingJudge()
    case = build_case(a_log("booking-with-no-gate"), tools=BOOKING)

    score = await measured(NoForbiddenToolRanJudge(["transfer"], case.gate), case, judge)

    assert score.score == 1.0
    assert "none of the 1 forbidden tool(s) ran" in score.reason
    assert judge.prompts == []


# The whole point of the field, and it is the policy experiment's finding written down: the
# turn below is what haiku actually said while booking unasked, and `no-reserva-antes-del-si`
# bans none of it.
async def test_the_phrases_hold_on_the_very_call_the_forbidden_tool_judge_breaks() -> None:
    case = a_case_of(
        asked("Me viene bien la de las cuatro."),
        replied("Voy a reservarle la cita. Le llega un SMS con la confirmación.", seq=13),
        gate=[ran("book", seq=12)],
    )
    banned = ["queda reservada", "he reservado", "está reservada"]

    assert (await measured(NothingWasSaidJudge(banned), case, CountingJudge())).score == 1.0
    broke = await measured(NoForbiddenToolRanJudge(["book"], case.gate), case, CountingJudge())
    assert broke.score == 0.0
    assert broke.reason == "the golden forbids book, and this call ran book at seq 12"


# ── the words ───────────────────────────────────────────────────────────────────


async def test_a_phrase_is_found_in_whichever_turn_the_agent_put_it_in() -> None:
    case = a_conversation("Un momento.", "El martes a las 10:15.")

    assert (await measured(EveryPhraseWasSaidJudge(["10:15"]), case, CountingJudge())).score


async def test_a_phrase_is_matched_the_way_a_person_reads_it_and_not_by_case() -> None:
    case = a_conversation("La consulta cuesta 45 Euros.")

    assert (await measured(EveryPhraseWasSaidJudge(["45 euros"]), case, CountingJudge())).score


async def test_a_phrase_the_agent_never_said_names_itself_in_the_reason() -> None:
    score = await measured(
        EveryPhraseWasSaidJudge(["gratis"]), a_conversation("Cuesta 45 euros."), CountingJudge()
    )

    assert score.score == 0.0
    assert "'gratis'" in score.reason


async def test_a_forbidden_phrase_is_caught_and_the_turn_that_said_it_is_named() -> None:
    case = a_conversation("Un momento.", "La primera consulta es gratis.")

    score = await measured(NothingWasSaidJudge(["gratis"]), case, CountingJudge())

    assert score.score == 0.0
    assert "'gratis' in agent turn 2" in score.reason


async def test_silence_holds_when_none_of_the_forbidden_phrases_was_said() -> None:
    case = a_conversation("La consulta cuesta 45 euros.")

    assert (await measured(NothingWasSaidJudge(["gratis"]), case, CountingJudge())).score == 1.0


# ── the fact that arrived mid-call ──────────────────────────────────────────────


async def took_it_up(replies: bool, case: Case) -> float:
    """What the replies judge answered about this call, for the golden that expects `replies`."""
    return (await measured(TheEventWasAnsweredJudge(replies, case), case, CountingJudge())).score


async def test_an_agent_that_names_what_the_event_carried_has_taken_it_up() -> None:
    judge = CountingJudge()
    case = with_an_event("slot_freed", {"at": "10:15"}, at=3)

    assert (await measured(TheEventWasAnsweredJudge(True, case), case, judge)).score == 1.0
    assert judge.prompts == []


# Whether the agent speaks the moment the fact lands or on the caller's next question is the app's
# business; what the golden is about is whether the caller was told.
async def test_the_reply_that_followed_is_the_agent_turn_after_it_whenever_it_came() -> None:
    case = with_an_event("slot_freed", {"at": "10:15"}, at=3)

    assert case.turns[-1].text.endswith("10:15.")
    assert await took_it_up(False, case) == 0.0


async def test_an_agent_that_says_nothing_the_event_carried_has_left_it_alone() -> None:
    case = with_an_event("promo_started", {"code": "VERANO"}, at=3)

    assert await took_it_up(False, case) == 1.0
    assert await took_it_up(True, case) == 0.0


async def test_a_golden_that_expects_a_reply_to_an_event_that_never_arrived_is_broken() -> None:
    """A check that could not look must never read as proof: no event, no verdict but broken."""
    case = a_conversation("Un momento.")

    score = await measured(TheEventWasAnsweredJudge(True, case), case, CountingJudge())

    assert score.score == 0.0
    assert "no event.received reached the call" in score.reason

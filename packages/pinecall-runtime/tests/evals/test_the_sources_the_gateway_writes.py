"""Ring 4 reads `docs.sources` as the gateway writes it: a searched price is a grounded price."""

from __future__ import annotations

import pytest

from pinecall.evals.hangup_score import score_call
from pinecall.settings import Settings
from pinecall_protocol.events import Judgment
from tests.lookups.fakes import CALL, ScriptedKnowledge, a_chunk, a_config, a_served_call

pytestmark = pytest.mark.unit

SEARCHING = {"query": "¿cuánto cuesta la revisión?"}

# The budget every unit test runs under: the grounded judge answers by code alone, off the log.
NO_BUDGET = Settings(world="production", judge_ceiling_eur=0)


async def test_the_grounded_judge_finds_a_stated_price_in_the_sources_the_fill_wrote() -> None:
    """The one chain that has to hold: search → docs.sources on the log → evidence → verdict."""
    served = a_served_call(
        knowledge=ScriptedKnowledge(
            answers=[a_chunk("c1", "Tarifas › Revisión", "Tarifas › Revisión\n\nRevisión: 45 €.")]
        )
    )
    await served.heard("¿cuánto cuesta la revisión?")
    await served.lookups.lookup(CALL, "search", SEARCHING, "sp_1")
    await served.said("La revisión son 45 €.")

    scored = await score_call(await served.log.whole(), a_config(), NO_BUDGET)
    grounded = _judged(scored.judges, "grounded")
    assert grounded.verdict == "held"
    assert grounded.reason == "all 1 stated fact(s) appear in the evidence"


async def test_a_price_the_search_never_found_is_still_ungrounded() -> None:
    served = a_served_call(knowledge=ScriptedKnowledge(answers=[]))
    await served.heard("¿cuánto cuesta?")
    await served.lookups.lookup(CALL, "search", {"query": "¿cuánto cuesta?"}, "sp_1")
    await served.said("La revisión son 45 €.")

    scored = await score_call(await served.log.whole(), a_config(), NO_BUDGET)
    assert _judged(scored.judges, "grounded").verdict != "held"


def _judged(judges: list[Judgment], name: str) -> Judgment:
    """The one judge's row on the entry."""
    return next(judge for judge in judges if judge.name == name)

"""How every test here measures a judge: through the matrix, so the matrix is never untested."""

from __future__ import annotations

from typing import Any

from livekit.agents.evals import Evaluator
from livekit.agents.llm import LLM

from pinecall.evals import Case, Score, Spoken, a_matrix


async def measured(judge: Evaluator, case: Case, llm: LLM[Any]) -> Score:
    """One judge over one case: the score, the sentence it wrote, and what it asked the model."""
    matrix = await a_matrix(
        [Spoken(model="fixture", golden=case.name or "case", case=case)], [judge], llm
    )
    return matrix.runs[0].scores[0]

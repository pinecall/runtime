"""A judge that answers by code: livekit's own Judge shape, and not one question put to a model."""

from __future__ import annotations

from typing import Any, override

from livekit.agents.evals import Judge, JudgmentResult
from livekit.agents.llm import LLM, ChatContext

# A hard policy has a right answer, and a model asked for it is a model that can be wrong about it.
# livekit's base class says how to keep the shape without paying for a prompt, in as many words:
# "Subclass and override evaluate to implement deterministic or programmatic checks that don't need
# an LLM" (evals/judge.py:166-184). Every policy in this package is one of those, so the signature
# is written once, here, and each of them writes only the sentence it decides by.


class PolicyJudge(Judge):
    """One binary question about a call, answered from the evidence and never asked of a model."""

    def __init__(self, *, name: str, criteria: str) -> None:
        super().__init__(name=name)
        self.criteria = criteria

    # livekit's own signature, kept whole: a judge that narrowed it would stop being an Evaluator
    # (evals/evaluation.py:17-31) and could not ride in a JudgeGroup beside their eight.
    @override
    async def evaluate(
        self,
        *,
        chat_ctx: ChatContext,
        reference: ChatContext | None = None,
        llm: LLM[Any] | None = None,
    ) -> JudgmentResult:
        """The verdict, with the question that was answered hung on it as livekit hangs it."""
        result = self.decide(chat_ctx)
        # The same line their LLM judge writes after it has judged (evals/judge.py:250), so a
        # reader of a mixed report finds the criteria in the same field whoever answered.
        result.instructions = self.criteria
        return result

    def decide(self, chat_ctx: ChatContext) -> JudgmentResult:
        """The whole of a subclass: this call's evidence in, one verdict and its sentence out."""
        raise NotImplementedError(f"the policy '{self.name}' decides nothing")


def held(reason: str) -> JudgmentResult:
    """The policy held, and the reason says what this call carried that proves it."""
    return JudgmentResult(verdict="pass", reasoning=reason)


def broken(reason: str) -> JudgmentResult:
    """The policy did not hold, and the reason names the evidence in the log that says so."""
    return JudgmentResult(verdict="fail", reasoning=reason)

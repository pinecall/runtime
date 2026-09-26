"""The persona's verdict: did the caller get what it came for, by the rule it wrote for itself."""

from __future__ import annotations

from typing import Any, override

from livekit.agents.evals import Judge, JudgmentResult
from livekit.agents.llm import LLM, ChatContext

from pinecall.evals.case import Case
from pinecall.evals.judges.binary_question import ask_judge
from pinecall.evals.judges.code_judge import broken

# The name the log files the verdict under. `held` is the caller accepting the call and `broken`
# the caller declining it — the four words are the log's (docs/decisions/scoring.md) — and the
# reason opens with the caller's own word, so a pane that shows one line shows that one.
NAME = "persona"

CRITERIA = (
    "The caller hangs up satisfied: what they came for happened on the call, by their own rule, "
    "and nothing that makes them decline it did."
)

# The rule is the persona's own prose about the call, and the only thing that can read prose
# against a conversation is a model — there is no half of this a regex settles first, which is
# what sets it apart from grounded and promises. So the whole judge is one question in livekit's
# shape (asking.py), built from whichever halves the caller wrote; a half it did not write is not
# asked about, because an empty rule read aloud is a question about nothing.
ACCEPTS = "The caller accepts the call only if this happened on it: {accepts_when}"
DECLINES = "The caller declines the call if this happened on it: {declines_when}"
THE_QUESTION = (
    "Answer 'pass' if the caller accepts the call, 'fail' if they decline it, and 'maybe' if the "
    "conversation does not say. Judge what actually happened — both sides' turns and the tool "
    "calls — never how either side worded it, and never the caller's own claim to be satisfied: "
    "the rule is the measure."
)

# The caller playing itself would be generous about its own conversation, which is why the rule
# never reaches the improvising model (evals/simulated_caller.py) and the judge is the platform's
# one Haiku, on the box's key, as every judge on the panel is (judges/model.py). LiveKit's own
# simulations draw the same line: the simulator's verdict is a judge over the transcript, not the
# simulated user's opinion of it (livekit/agents/simulation.py, `simulator_verdict`).
ACCEPTED = "accepted: {reason}"
DECLINED = "declined: {reason}"
COULD_NOT_SAY = "could not say: {reason}"

NOBODY_TO_ASK = "the caller wrote a rule for this call, and no judge model was given to read it"


class PersonaJudge(Judge):
    """One question, put to a model: did this call meet the caller's own rule for accepting it."""

    def __init__(self, accepts_when: str, declines_when: str) -> None:
        super().__init__(name=NAME)
        self._accepts_when = accepts_when
        self._declines_when = declines_when

    # Without a model there is no answer at all, and `broken` is how the panel says so: score.py
    # files a judge that wanted a model and was refused one as `skipped`, with the ceiling's
    # reason, exactly as it does for grounded and promises. Never a pass the call did not earn.
    @override
    async def evaluate(
        self,
        *,
        chat_ctx: ChatContext,
        reference: ChatContext | None = None,
        llm: LLM[Any] | None = None,
    ) -> JudgmentResult:
        """The caller's rule against the call, asked of the judge model; unanswered without one."""
        if llm is None:
            settled = broken(NOBODY_TO_ASK)
            settled.instructions = CRITERIA
            return settled
        answered = await ask_judge(llm, self.criteria(), chat_ctx)
        answered.reasoning = _in_the_callers_word(answered)
        return answered

    def criteria(self) -> str:
        """The question, carrying whichever halves of the rule the caller wrote."""
        halves = (
            ACCEPTS.format(accepts_when=self._accepts_when) if self._accepts_when else "",
            DECLINES.format(declines_when=self._declines_when) if self._declines_when else "",
        )
        return "\n".join([*(half for half in halves if half), THE_QUESTION])


def persona_judge_of(case: Case) -> PersonaJudge | None:
    """The judge for one call when the caller on it wrote a rule; None when nobody did."""
    if case.persona_rule is None:
        return None
    accepts_when, declines_when = case.persona_rule
    return PersonaJudge(accepts_when, declines_when)


def _in_the_callers_word(answered: JudgmentResult) -> str:
    """The model's sentence, opened with the caller's verdict so a pane says it in one word."""
    if answered.passed:
        return ACCEPTED.format(reason=answered.reasoning)
    if answered.failed:
        return DECLINED.format(reason=answered.reasoning)
    return COULD_NOT_SAY.format(reason=answered.reasoning)

"""The hang-up panel over a finished call, and the judges a golden is scored with."""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Never, override

from livekit.agents import APIError, llm
from livekit.agents.evals import Judge, JudgmentResult
from livekit.agents.llm import ChatContext

from pinecall.domain.agent import AgentConfig, block_hash
from pinecall.domain.errors import PinecallError
from pinecall.domain.judging import JudgeSpec
from pinecall.domain.names import JsonObject
from pinecall.evals import _asking, catalog
from pinecall.evals._asking import Answered, Context
from pinecall.evals._evidence import as_text, evidence_of
from pinecall.evals.case import Case, case_of
from pinecall.evals.catalog import Seated, Surroundings
from pinecall.providers import prices
from pinecall.providers.build import Running, llm_of
from pinecall.providers.catalog import Providers
from pinecall.wire.frames import Entry
from pinecall.wire.metrics import LLMModelUsage
from pinecall.wire.scores import CallScore, Judgment, JudgmentEvidence

logger = logging.getLogger(__name__)


OVER_THE_CEILING = "judging this call reached its ceiling of ${ceiling} before this judge asked"


OUT_OF_EVALS = "the org's evals for the month are used up: this judge was not asked"


NOTHING_ANSWERED = "every judge run over this call failed and not one of them answered"


@dataclass(frozen=True)
class JudgeModel:
    """The judge model a call is asked on, what one call may spend, and the org's evals left."""

    running: Running | None
    ceiling_usd: float
    unjudged: str = ""
    # None: the org's evals are not counted against a limit.
    evals_left: int | None = None


@dataclass(frozen=True)
class Panel:
    """What a call is judged by: the library's switches, the org's and the agent's own, and more."""

    switches: Mapping[str, bool] = field(default_factory=dict[str, bool])
    own: Sequence[JudgeSpec] = ()
    around: Surroundings = field(default_factory=lambda: Surroundings(org=""))
    prompt: str = ""


class CaseJudge(Judge):
    """A golden's expectation, settled by code from the call when the golden is scored."""

    def __init__(self, name: str, criteria: str, settled: JudgmentResult) -> None:
        """The expectation, what it holds the call to, and the verdict code settled."""
        super().__init__(name=name)
        self.criteria = criteria
        self.settled = settled
        self.calls = 0

    @override
    async def evaluate(
        self,
        *,
        chat_ctx: ChatContext,
        reference: ChatContext | None = None,
        llm: llm.LLM[Never] | None = None,
    ) -> JudgmentResult:
        """The verdict code settled, with what it held the call to."""
        result = JudgmentResult(verdict=self.settled.verdict, reasoning=self.settled.reasoning)
        result.instructions = self.criteria
        return result


class AskedJudge(Judge):
    """A judge of the panel asked of one case from a golden: N/A and a classification hold."""

    def __init__(self, seated: Seated, case: Case, context: Context) -> None:
        """The judge, the case it is asked of, and what it may read beyond the call."""
        super().__init__(name=seated.spec.name)
        self.seated = seated
        self.case = case
        self.context = context
        self.calls = 0

    @override
    async def evaluate(
        self,
        *,
        chat_ctx: ChatContext,
        reference: ChatContext | None = None,
        llm: llm.LLM[Never] | None = None,
    ) -> JudgmentResult:
        """The panel's answer read as a golden's: broken fails, anything else holds."""
        gate = catalog.gated(self.seated, self.case)
        if gate is not None:
            return _result(passing(gate), self.seated.spec.question)
        if llm is None:
            return _result(failing(f"{self.name} needs the judge model, and none was given"), "")
        answered = await _asking.ask(self.seated.spec, self.case, self.context, llm)
        self.calls += len(answered.spent)
        ruled = failing if answered.verdict == "broken" else passing
        return _result(ruled(answered.reason), self.seated.spec.question)


# A golden's expectation settled by code, or a judge of the panel asked of the golden's call.
type GoldenJudge = CaseJudge | AskedJudge


def asked_judges(case: Case, panel: Panel) -> dict[str, AskedJudge]:
    """Every judge a golden may name, by name: the whole library, the org's and the agent's own."""
    context = context_of(case, panel)
    every = [Seated(judge.spec, judge.gate, judge.version) for judge in catalog.library().values()]
    every += [Seated(spec) for spec in panel.own]
    return {seated.spec.name: AskedJudge(seated, case, context) for seated in every}


# A question written under a library judge's name is asked as written, without the library's gate.
def alone(spec: JudgeSpec, panel: Panel) -> Panel:
    """The panel with one judge on it: the library's as written, or one written to be tried."""
    library = catalog.library()
    found = library.get(spec.name)
    if found is not None and found.spec == spec:
        return replace(panel, switches={name: name == spec.name for name in library}, own=[])
    return replace(panel, switches=dict.fromkeys(library, False), own=[spec])


async def at_hangup(
    entries: Sequence[Entry],
    declared: AgentConfig | None,
    panel: Panel,
    judge: JudgeModel,
    *,
    configured: Providers,
) -> CallScore:
    """The panel over a finished call: each judge gated, then asked under the ceiling and evals."""
    case = case_of(entries, declared)
    seated = catalog.panel_of(case, panel.switches, panel.own)
    context = context_of(case, panel)
    running = judge.running
    model = None if running is None else llm_of(running)
    judged: list[Judgment] = []
    spent: list[LLMModelUsage] = []
    evals = 0
    try:
        for member in seated:
            gate = catalog.gated(member, case)
            if gate is not None:
                judged.append(_judgment(member, Answered("na", gate), entries))
                continue
            refused = _refused(judge, model, _priced(spent, running, configured), evals)
            if refused is not None:
                judged.append(_judgment(member, Answered("skipped", refused), entries))
                continue
            answered = await _answered(member, case, context, model, judge.unjudged)
            spent += answered.spent
            evals += int(answered.is_an_eval)
            judged.append(_judgment(member, answered, entries))
    finally:
        if model is not None:
            await model.aclose()
    cost = _priced(spent, running, configured)
    return _scored(judged, seated, (evals, len(spent)), cost, running)


def context_of(case: Case, panel: Panel) -> Context:
    """What the judges of a case may read beyond the conversation."""
    return Context(
        prompt=panel.prompt,
        evidence=as_text(evidence_of(case)),
        facts=catalog.facts_of(case, panel.around),
    )


def passing(reason: str) -> JudgmentResult:
    """A verdict that passes."""
    return JudgmentResult(verdict="pass", reasoning=reason)


def failing(reason: str) -> JudgmentResult:
    """A verdict that fails."""
    return JudgmentResult(verdict="fail", reasoning=reason)


def evidence_at(positions: Sequence[int], entries: Sequence[Entry]) -> JudgmentEvidence:
    """The log positions an answer rests on that the log has, and the first words said at one."""
    by_seq = {entry.seq: entry for entry in entries}
    seqs = [seq for seq in positions if seq in by_seq]
    words = (by_seq[seq].data.get("text") for seq in seqs)
    spoken = next((text for text in words if isinstance(text, str) and text), None)
    return JudgmentEvidence(seqs=seqs, said=spoken) if spoken else JudgmentEvidence(seqs=seqs)


# The evals are spent in panel order: the library's first, so the org's own are the ones skipped.
def _refused(
    judge: JudgeModel, model: llm.LLM[Never] | None, spent_usd: float, evals: int
) -> str | None:
    if model is None:
        return judge.unjudged
    if spent_usd >= judge.ceiling_usd:
        return OVER_THE_CEILING.format(ceiling=judge.ceiling_usd)
    if judge.evals_left is not None and evals >= judge.evals_left:
        return OUT_OF_EVALS
    return None


async def _answered(
    member: Seated, case: Case, context: Context, model: llm.LLM[Never] | None, unjudged: str
) -> Answered:
    if model is None:
        return Answered("skipped", unjudged)
    try:
        return await _asking.ask(member.spec, case, context, model)
    except (APIError, PinecallError) as failed:
        logger.warning(
            "judge %s failed; the call is sealed without it", member.spec.name, exc_info=True
        )
        return Answered("skipped", f"the judge model failed: {failed}")


def _judgment(member: Seated, answered: Answered, entries: Sequence[Entry]) -> Judgment:
    return Judgment(
        name=member.spec.name,
        verdict=answered.verdict,
        criteria=member.spec.question,
        reason=answered.reason,
        evidence=evidence_at(answered.positions, entries),
        choice=answered.choice,
        score=answered.score,
    )


# `counted` is the evals billed and the model calls made, the trigger questions among them.
def _scored(
    judged: Sequence[Judgment],
    seated: Sequence[Seated],
    counted: tuple[int, int],
    cost_usd: float,
    running: Running | None,
) -> CallScore:
    evals, calls = counted
    settled = [judgment for judgment in judged if judgment.verdict in {"held", "broken"}]
    answered = [judgment for judgment in judged if judgment.verdict not in {"skipped", "deferred"}]
    scored: JsonObject = {
        "judges": [judgment.written() for judgment in judged],
        "panel": [member.spec.name for member in seated],
        "judge_calls": calls,
        "evals": evals,
    }
    if running is not None and not running.lent:
        scored["own_key"] = True
    if settled:
        scored["passed"] = not any(judgment.verdict == "broken" for judgment in settled)
    elif not answered:
        scored["not_judged"] = NOTHING_ANSWERED if running is not None else _why_not(judged)
    if cost_usd:
        scored["judge_cost_usd"] = cost_usd
    if judged:
        scored["judged_by"] = _judged_by_of(judged, running)
    return CallScore.model_validate(scored)


def _why_not(judged: Sequence[Judgment]) -> str:
    return next((judgment.reason for judgment in judged if judgment.reason), NOTHING_ANSWERED)


# Priced by the names the operator configured, which the rates are keyed by.
def _priced(
    spent: Sequence[LLMModelUsage], running: Running | None, configured: Providers
) -> float:
    if running is None or not spent:
        return 0.0
    named = [
        usage.model_copy(update={"provider": running.vendor, "model": running.model or usage.model})
        for usage in spent
    ]
    return prices.cost(named, configured).usd


def _result(settled: JudgmentResult, criteria: str) -> JudgmentResult:
    settled.instructions = criteria
    return settled


# The panel's questions in its order: the same questions hash the same, whatever the call said.
def _judged_by_of(judged: Sequence[Judgment], running: Running | None) -> JsonObject:
    """The judge model and one hash of every question asked, as call.score carries them."""
    questions = "\n".join(f"{judgment.name}\n{judgment.criteria}" for judgment in judged)
    return {
        "provider": None if running is None else running.vendor,
        "model": None if running is None else running.model,
        "criteria": block_hash(questions),
    }

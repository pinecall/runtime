"""One finished call judged at hang-up: its own log in, the call.score that seals it out."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from livekit.agents.evals import Evaluator, JudgeGroup, JudgmentResult
from livekit.agents.llm import ChatContext
from livekit.agents.metrics.usage import ModelUsageCollector

from pinecall._settings import Settings, load_settings
from pinecall.evals.bridge import a_case
from pinecall.evals.case import Case
from pinecall.evals.judges.consent import ConsentJudge
from pinecall.evals.judges.grounded import EXTRACTORS, GroundedJudge, evidence_of
from pinecall.evals.judges.model import Counted, a_judge
from pinecall.evals.judges.persona import persona_judge_of
from pinecall.evals.judges.policy import PolicyJudge
from pinecall.evals.judges.promises import promises_of
from pinecall.evals.verdicts import a_judgment, nobody_asked
from pinecall.log.entry import Entry
from pinecall.providers import prices
from pinecall.providers.usage_wire import as_wire_rows
from pinecall.session.scoring import Scorer
from pinecall.types import AgentConfig
from pinecall_protocol.events import CallScore, Judgment

logger = logging.getLogger(__name__)

JUDGING_BROKE = "judging this call failed: {broke}"

NOTHING_ANSWERED = "every judge run over this call failed and not one of them answered"

OVER_THE_CEILING = "judging this call may spend {ceiling} EUR on a model and was given none"

JUDGING_OFF = "this org's calls are not judged at hang-up: POST /v1/evals/judge/{call} judges one"


async def a_score(
    entries: Sequence[Entry], config: AgentConfig, settings: Settings | None = None
) -> CallScore:
    """Every judge this call can be given, over its own log. Never raises: the log seals on it."""
    try:
        return await _judged(entries, config, settings or load_settings())
    except Exception as broke:
        logger.warning("call %s: nothing judged it", _call_of(entries), exc_info=True)
        return _nobody_judged(JUDGING_BROKE.format(broke=broke))


async def _judged(entries: Sequence[Entry], config: AgentConfig, settings: Settings) -> CallScore:
    """The judges, the questions the call's budget allowed, and what those questions cost."""
    case = a_case(
        entries,
        tools=config.tools_by_name,
        knowledge=[config.knowledge] if config.knowledge else None,
    )
    # A judge that answers by code costs nothing; one that may reach a model runs only while this
    # call's judging budget is above what judging it has already cost — which, before the first
    # question, is nothing. So a ceiling of zero is a door nobody passes, no judge model is built
    # at all; a per-org running budget is the operator's to add. docs/decisions/scoring.md.
    panel = _the_judges_of(case)
    spent = ModelUsageCollector()
    counted = _a_counted_judge(settings, spent) if settings.judge_ceiling_eur > 0 else None
    # The panel as the entry carries it: who was RUN, whatever any of them went on to answer.
    declared = [judge.name for judge in panel]
    # livekit's JudgeGroup is the runner whenever there is a model to hand it. A call nobody may
    # ask about has no model and no group to be in: every judge answers from code alone, which for
    # a policy is the whole of what it ever does anyway.
    if counted is None:
        why = OVER_THE_CEILING.format(ceiling=settings.judge_ceiling_eur)
        answered = [await _by_code_alone(one, case.chat_ctx, entries, why) for one in panel]
        return _an_entry([row for row in answered if row is not None], declared, 0, None)
    try:
        result = await JudgeGroup(llm=counted, judges=panel).evaluate(case.chat_ctx)
    finally:
        await counted.aclose()
    judged = [a_judgment(name, one, entries) for name, one in result.judgments.items()]
    cost = prices.eur_of(as_wire_rows(spent.flatten()))
    return _an_entry(judged, declared, counted.calls, cost)


# Whoever opens a call hands the session its judge, and knows whose call it is: the gateway for a
# written call reads the org's setting in-process, the worker for a spoken one asks the gateway.
# Asked at hang-up and not when the call opened, so a setting turned mid-call is the one that holds.
# A value and not a closure, so what a door handed a session can be read back and compared.
@dataclass(frozen=True)
class JudgedWhen:
    """A scorer that asks first whether this call's org judges its calls, and judges if it does."""

    judges: Callable[[str], Awaitable[bool]]
    score: Scorer = a_score

    async def __call__(self, entries: Sequence[Entry], config: AgentConfig) -> CallScore:
        """No verdict and the reason when the org declined judging; the judges otherwise."""
        if not await self.judges(_call_of(entries)):
            return _nobody_judged(JUDGING_OFF)
        return await self.score(entries, config)


# The policies are the tenant's rules, and a live call carries its own evidence for these three.
# `register` (evals/judges/register.py) waits on a declaration the agent does not carry — the
# register the business asked for is not on AgentConfig; whoever declares it adds the line here.
# Inventing it would judge a rule nobody wrote down. The fourth is the CALLER's rule, and only a
# synthetic caller writes one: a person on the phone is judged by the first three alone.
def _the_judges_of(case: Case) -> list[Evaluator]:
    """Every judge a live call carries its own evidence for, in the order they are declared."""
    panel: list[Evaluator] = [
        ConsentJudge(case.gate),
        GroundedJudge(EXTRACTORS, evidence_of(case)),
        promises_of(case),
    ]
    if (persona := persona_judge_of(case)) is not None:
        panel.append(persona)
    return panel


# The tally of the questions actually put to a model, and livekit's own collector behind it fed by
# the model's own `metrics_collected`: a judgment's tokens are an LLM row like every other in this
# runtime, and nothing here counts one itself.
def _a_counted_judge(settings: Settings, spent: ModelUsageCollector) -> Counted:
    """The one model this call's judging may ask, wrapped in the count of what it was asked."""
    judge_model = a_judge(settings)
    judge_model.on("metrics_collected", spent.collect)  # pyright: ignore[reportUnknownMemberType] — livekit's callback is `(...) -> Unknown`
    return Counted(judge_model)


# livekit's own two-step, with the second step closed: a check that can settle itself settles
# itself even with no model to reach (evals/judge.py:327-367). What a policy answers is its whole
# answer; what a judge that wanted a model could not settle is `skipped` with the ceiling's reason,
# never a fail the call did not earn.
async def _by_code_alone(
    judge: Evaluator, chat_ctx: ChatContext, entries: Sequence[Entry], why: str
) -> Judgment | None:
    """One judge with no model to reach: a policy's answer stands, and anything else is skipped."""
    settled = await _settled(judge, chat_ctx)
    if settled is None:
        return None
    if settled.passed or isinstance(judge, PolicyJudge):
        return a_judgment(judge.name, settled, entries)
    return nobody_asked(judge.name, settled, why, entries)


# A judge that broke answers nothing, and nothing is what its row says: no verdict is invented for
# it, because a fabricated `maybe` would read as a judge that looked and was unsure. It is dropped
# — the same drop livekit's own group does (evals/evaluation.py:155-165) — and if every judge is
# dropped the entry says so instead of claiming the call passed.
async def _settled(judge: Evaluator, chat_ctx: ChatContext) -> JudgmentResult | None:
    """What the judge answers with no model at all; None when it could not answer at all."""
    try:
        # `Evaluator.evaluate` takes livekit's own `LLM`, a `Generic[TEvent]` left bare in
        # its signature (evals/judge.py:198), so pyright reads the method as partially
        # unknown. The parameter is livekit's to name, not ours.
        return await judge.evaluate(  # pyright: ignore[reportUnknownMemberType]
            chat_ctx=chat_ctx, reference=None, llm=None
        )
    except Exception as broke:
        logger.warning("judge '%s' failed: %s", judge.name, broke)
        return None


# `passed` is the answer to a question somebody asked, so a call nobody asked about has none: it is
# ABSENT rather than true, and `not_judged` says why nobody did. And judge_cost_eur is absent when
# no usage row could be priced, never 0.0 — a model nobody reported tokens for has an unknown bill,
# not a free one. encode() drops what was never set, so absent here is absent on the wire.
# `panel` is the judges that were RUN and `judges` the ones that answered, so a judge that raised
# is in the first and not the second — the one fact a reader cannot recover from the rows.
def _an_entry(
    judged: list[Judgment], panel: list[str], judge_calls: int, cost: float | None
) -> CallScore:
    """The entry as the log carries it: what held, what asked, and what the asking cost."""
    scored: dict[str, Any] = {"judges": judged, "panel": panel, "judge_calls": judge_calls}
    if judged:
        scored["passed"] = not any(one.verdict == "broken" for one in judged)
    else:
        scored["not_judged"] = NOTHING_ANSWERED
    if cost is not None:
        scored["judge_cost_eur"] = cost
    return CallScore.model_validate(scored)


# A call still gets its entry when nothing judged it, because the log seals on this entry. What it
# does NOT get is a verdict: a reader that trusts `passed` alone would report a green call nobody
# looked at, and it would be right to. The reason belongs in the tenant's log and not only in the
# process's, which is the one nobody ships.
def _nobody_judged(why: str) -> CallScore:
    """The entry a call gets when no judge answered: no verdict, and the reason there is none."""
    return CallScore(judges=[], panel=[], judge_calls=0, not_judged=why)


def _call_of(entries: Sequence[Entry]) -> str:
    """Which call this was, for the one line the process log gets when nothing judged it."""
    return next((entry.call for entry in entries if entry.call is not None), "")

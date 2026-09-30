"""The judges: settled by code or by one question to a model; the panel every call meets."""

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Never, override

from livekit.agents import APIError, llm
from livekit.agents.evals import Judge, JudgmentResult, Verdict
from livekit.agents.llm import ChatContext, ChatItem, ChatMessage, FunctionCall, FunctionCallOutput

from pinecall.domain.agent import AgentConfig, AgentJudge
from pinecall.domain.errors import PinecallError, UpstreamFailed
from pinecall.domain.names import JsonObject
from pinecall.evals import compliance
from pinecall.evals._evidence import (
    as_text,
    carries,
    committed_in,
    evidence_of,
    missing_from,
    stated_in,
    tool_call_text,
)
from pinecall.evals.case import (
    AGENT,
    CALLER,
    PUNCTUATION,
    Case,
    as_chat,
    calls_of,
    case_of,
    said_by,
    verdict_word,
)
from pinecall.evals.checks import consent_of
from pinecall.evals.compliance import Compliance, Panel
from pinecall.providers import prices
from pinecall.providers.build import Running, a_mapping, completion_usage, llm_of
from pinecall.providers.catalog import Providers
from pinecall.wire.frames import Entry
from pinecall.wire.metrics import LLMModelUsage
from pinecall.wire.rest.evals import Golden, Register
from pinecall.wire.scores import CallScore, Judgment, JudgmentEvidence

logger = logging.getLogger(__name__)


# Asked as livekit's own judge asks: a forced tool call at temperature 0 (evals/judge.py).
JUDGE = (
    "You are an evaluator for a conversational AI agent. Answer the question below about the "
    "conversation, then call submit_verdict with 'pass', 'fail' or 'maybe' and a brief reason."
)


NO_VERDICT = "the judge model answered without calling submit_verdict"


# The verdict is read off the call's arguments; the tool itself never runs.
SUBMIT_VERDICT: JsonObject = {
    "name": "submit_verdict",
    "description": "Submit your verdict.",
    "parameters": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["pass", "fail", "maybe"],
                "description": "'pass' if the criteria are met, 'fail' if not, 'maybe' if unsure.",
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence saying what in the conversation decided it.",
            },
        },
        "required": ["verdict", "reasoning"],
    },
}


# livekit's transcript lines (evals/judge.py), tool calls included: a booking is an action.
SPOKE = "{role}: {text}"


A_SEQ = re.compile(r"\bseq (\d+)\b")


CONSENT = (
    "Every irreversible tool call in this conversation ran after a confirm.granted for the "
    "same tool call and the same audience."
)


UNDECLARED = "the agent's declaration was not at hand when the case was built"


HEARD = "Every line this golden puts in the caller's mouth reached the agent."


TOOLS = "Every tool this golden names was called in the conversation."


NOT_TOOLS = "The conversation called none of the tools this golden forbids."


SAYS = "The agent said every phrase this golden names."


SILENCE = "The agent said none of the phrases this golden names."


ANSWERED = "The agent answered every fact that arrived mid-call."


STAYED_QUIET = "The agent carried on without answering the facts that arrived mid-call."


# A check that could not look must not pass.
NO_EVENT = "this golden expects a reply to an event, and no event.received reached the call"


REGISTER = "The agent addressed the caller as {register} in every one of its turns."


# Unmistakable tú; `té` (tea) carries its accent, so `te` never matches it.
TUTEO: frozenset[str] = frozenset(
    {"tú", "ti", "te", "contigo", "tu", "tus", "tuyo", "tuya", "tuyos", "tuyas"}
)


# `le`, `les`, `su` and `sus` are left out: they are as often the third person.
USTEO: frozenset[str] = frozenset({"usted", "ustedes", "consigo", "suyo", "suya", "suyos", "suyas"})


GROUNDED = "Every concrete fact the agent stated appears in the evidence this call carried."


GROUNDED_QUESTION = """Evidence available to the agent during this conversation:

{evidence}

Answer 'pass' only if every price, hour, date and person's name the assistant states in the
conversation below is supported by that evidence. Answer 'fail' if the assistant states one the
evidence does not support. Ignore anything the user said; judge only the assistant's turns."""


NOTHING_STATED = "the agent stated no price, hour, date or name in this call"


NOBODY_LOOKED = "{missing} — and no judge model was given, so nothing could look further"


PROMISES = "Every commitment the agent made on the business's behalf is recorded by a tool call."


PROMISES_QUESTION = """Tool calls this conversation made, each with what it answered:

{calls}

Answer 'fail' only if the assistant commits the business to something in the future — calling the
caller back, a visit, a price, sending something, a follow-up — that none of those tool calls
records. Answer 'pass' if every such commitment is backed by one of them, or if the assistant
commits to nothing. Ignore what the user said; judge only the assistant's turns."""


NO_TOOL_CALLS = "(none: the conversation called no tool)"


NOTHING_PROMISED = "the agent committed the business to nothing"


NOBODY_CHECKED = (
    "{promised} — and no judge model was given, so nobody could tell whether a tool did"
)


PERSONA = (
    "The caller hangs up satisfied: what they came for happened on the call, by their own rule, "
    "and nothing that makes them decline it did."
)


ACCEPTS = "The caller accepts the call only if this happened on it: {accepts_when}"


DECLINES = "The caller declines the call if this happened on it: {declines_when}"


# A separate model decides, never the one playing the caller: it would grade itself kindly.
PERSONA_QUESTION = (
    "Answer 'pass' if the caller accepts the call, 'fail' if they decline it, and 'maybe' if the "
    "conversation does not say. Judge what actually happened — both sides' turns and the tool "
    "calls — never how either side worded it, and never the caller's own claim to be satisfied: "
    "the rule is the measure."
)


NOBODY_READ = "the caller wrote a rule for this call, and no judge model was given to read it"


IN_THE_CALLERS_WORDS: Mapping[Verdict, str] = {
    "pass": "accepted: {reason}",
    "fail": "declined: {reason}",
    "maybe": "could not say: {reason}",
}


AS_ANSWERED: Mapping[Verdict, str] = {"pass": "{reason}", "fail": "{reason}", "maybe": "{reason}"}


# The org's own question, asked as the caller's rule is: of what happened, not of the wording.
OWN_QUESTION = (
    "{question}\n\nAnswer 'pass' if the conversation shows this held, 'fail' if it shows it did "
    "not, and 'maybe' if the conversation does not say. Judge what actually happened — both "
    "sides' turns and the tool calls — never how either side worded it."
)


NOBODY_ANSWERED = "the org wrote this question for the agent, and no judge model was given to ask"


OVER_THE_CEILING = "judging this call reached its ceiling of ${ceiling} before this judge asked"


NOTHING_ANSWERED = "every judge run over this call failed and not one of them answered"


@dataclass(frozen=True)
class Question:
    """What a model is asked, the reason given when there is no model, how its answer reads."""

    question: str
    unasked: str
    worded: Mapping[Verdict, str] = field(default_factory=lambda: AS_ANSWERED)


@dataclass(frozen=True)
class JudgeModel:
    """The judge model a call is asked on and what one call may spend on it, or None and why."""

    running: Running | None
    ceiling_usd: float
    unjudged: str = ""


@dataclass(frozen=True)
class Answer:
    """A judge model's verdict, and the tokens it cost."""

    result: JudgmentResult
    usage: LLMModelUsage | None


class CaseJudge(Judge):
    """A judge of one case: settled by code when it was built, or by one question to a model."""

    def __init__(self, name: str, criteria: str, settled: JudgmentResult | Question) -> None:
        """The judge, its criteria, and the verdict code settled or the question it asks."""
        super().__init__(name=name)
        self.criteria = criteria
        self.settled = settled
        self.calls = 0
        self.spent: list[LLMModelUsage] = []

    @property
    def needs_a_model(self) -> bool:
        """Whether code left the verdict to a model."""
        return isinstance(self.settled, Question)

    @override
    async def evaluate(
        self,
        *,
        chat_ctx: ChatContext,
        reference: ChatContext | None = None,
        llm: llm.LLM[Never] | None = None,
    ) -> JudgmentResult:
        """The settled verdict, or the model's answer to the question; broken with no model."""
        if isinstance(self.settled, JudgmentResult):
            return _with_criteria(self.settled, self.criteria)
        if llm is None:
            return _with_criteria(_failing(self.settled.unasked), self.criteria)
        self.calls += 1
        answered = await _ask_judge(llm, self.settled.question, chat_ctx)
        if answered.usage is not None:
            self.spent.append(answered.usage)
        result = answered.result
        result.reasoning = self.settled.worded[result.verdict].format(reason=result.reasoning)
        return result


def golden_judges(golden: Golden, case: Case) -> list[CaseJudge]:
    """Consent, then one judge per expectation the golden sets, in the order Expect names them."""
    expect = golden.expect
    judges = [_consent_judge(case)]
    # Without it an unheard caller would pass every "never did X".
    if golden.input:
        judges.append(_heard(case, len(golden.input)))
    if expect.tools:
        judges.append(_tools(case, expect.tools))
    if expect.not_tools:
        judges.append(_not_tools(case, expect.not_tools))
    if expect.not_said:
        judges.append(_silence(case, expect.not_said))
    if expect.says:
        judges.append(_says(case, expect.says))
    if expect.grounded:
        judges.append(_grounded_judge(case))
    if expect.addressed_as is not None:
        judges.append(_register_judge(case, expect.addressed_as))
    if expect.replies is not None:
        judges.append(_replies(case, replies=expect.replies))
    return judges


# The register a business asks for is not declared anywhere, so it is not judged at hang-up.
def hangup_judges(
    case: Case, own: Sequence[AgentJudge], org_facts: Compliance | None = None
) -> list[CaseJudge]:
    """The panel: consent, grounding, promises, compliance, the caller's rule, the agent's own."""
    panel = [_consent_judge(case), _grounded_judge(case), _promises_judge(case)]
    rules = [] if org_facts is None else compliance.ruled(case, org_facts)
    lawful = [CaseJudge(rule.name, rule.criteria, rule.settled) for rule in rules]
    ruled_by = [_persona_judge(case), *(_question_judge(case, judge) for judge in own)]
    return [*panel, *lawful, *(judge for judge in ruled_by if judge is not None)]


def evidence_in(reason: str, entries: Sequence[Entry]) -> JudgmentEvidence:
    """The seqs a reason names, each once, and the first words spoken at one of them."""
    seqs = list(dict.fromkeys(int(found) for found in A_SEQ.findall(reason)))
    by_seq = {entry.seq: entry for entry in entries}
    words = (by_seq[seq].data.get("said") for seq in seqs if seq in by_seq)
    spoken = next((text for text in words if isinstance(text, str) and text), None)
    return JudgmentEvidence(seqs=seqs, said=spoken) if spoken else JudgmentEvidence(seqs=seqs)


def judgment_of(name: str, result: JudgmentResult, entries: Sequence[Entry]) -> Judgment:
    """A judge's answer as call.score carries it, with the question it answered beside it."""
    return Judgment(
        name=name,
        verdict=verdict_word(result.verdict),
        criteria=result.instructions,
        reason=result.reasoning,
        evidence=evidence_in(result.reasoning, entries),
    )


# The seal never fails on a judge: a judge whose model broke is skipped, and says why.
async def at_hangup(
    entries: Sequence[Entry],
    declared: AgentConfig | None,
    judged_by: Panel,
    judge: JudgeModel,
    *,
    configured: Providers,
) -> CallScore:
    """The hang-up panel over a finished call: code judges always, model ones under the ceiling."""
    case = case_of(entries, declared)
    panel = hangup_judges(case, judged_by.own, judged_by.compliance)
    running, unjudged = judge.running, judge.unjudged
    model = None if running is None else llm_of(running)
    chat = as_chat(case)
    judged: list[Judgment] = []
    try:
        for member in panel:
            spent = _priced(panel, running, configured)
            if model is not None and member.needs_a_model and spent >= judge.ceiling_usd:
                over = OVER_THE_CEILING.format(ceiling=judge.ceiling_usd)
                judged.append(_skipped(member, over, entries))
                continue
            judged.append(await _answered(member, chat, model, entries, unjudged))
    finally:
        if model is not None:
            await model.aclose()
    settled = [judgment for judgment in judged if judgment.verdict in {"held", "broken"}]
    scored: JsonObject = {
        "judges": [judgment.written() for judgment in judged],
        "panel": [member.name for member in panel],
        "judge_calls": sum(member.calls for member in panel),
    }
    if settled:
        scored["passed"] = not any(judgment.verdict == "broken" for judgment in settled)
    else:
        scored["not_judged"] = NOTHING_ANSWERED if model is not None else unjudged
    if any(member.spent for member in panel):
        scored["judge_cost_usd"] = _priced(panel, running, configured)
    return CallScore.model_validate(scored)


# Priced by the names the operator configured, which the rates are keyed by.
def _priced(panel: Sequence[CaseJudge], running: Running | None, configured: Providers) -> float:
    if running is None:
        return 0.0
    spent = [
        usage.model_copy(update={"provider": running.vendor, "model": running.model or usage.model})
        for member in panel
        for usage in member.spent
    ]
    return prices.cost(spent, configured).usd


def _passing(reason: str) -> JudgmentResult:
    """A verdict that passes."""
    return JudgmentResult(verdict="pass", reasoning=reason)


def _failing(reason: str) -> JudgmentResult:
    """A verdict that fails."""
    return JudgmentResult(verdict="fail", reasoning=reason)


# livekit's judge keeps its instructions private and fixed at construction, so it is asked here.
async def _ask_judge(model: llm.LLM[Never], criteria: str, chat: ChatContext) -> Answer:
    """One question about the conversation to a model: pass, fail or maybe, and why."""
    question = ChatContext.empty()
    question.add_message(role="system", content=JUDGE)
    question.add_message(role="user", content=f"{criteria}\n\nConversation:\n{_spoken(chat)}")
    response = await model.chat(
        chat_ctx=question,
        tools=[llm.function_tool(_never_run, raw_schema=SUBMIT_VERDICT)],
        tool_choice="required",
        extra_kwargs={"temperature": 0.0},
    ).collect()
    if not response.tool_calls:
        raise UpstreamFailed(NO_VERDICT)
    answered: object = json.loads(response.tool_calls[0].arguments or "{}")
    fields: Mapping[str, object] = answered if a_mapping(answered) else {}
    result = JudgmentResult(
        verdict=_verdict_of(fields.get("verdict")), reasoning=str(fields.get("reasoning", ""))
    )
    result.instructions = criteria
    return Answer(result=result, usage=completion_usage(model, response.usage))


def _consent_judge(case: Case) -> CaseJudge:
    """Consent, from the gate trace; a trace with no declared side effect never passes."""
    read = consent_of(case.gate)
    if read.outcome == "undeclared":
        settled = _failing(f"{read.detail}: {UNDECLARED}")
    elif read.outcome == "broken":
        settled = _failing(read.detail)
    else:
        settled = _passing(read.detail)
    return CaseJudge("consent", CONSENT, settled)


def _register_judge(case: Case, expected: Register) -> CaseJudge:
    """Whether the agent never used the other register's words."""
    other = USTEO if expected == "tu" else TUTEO
    turns = said_by(case, AGENT)
    slips = [
        (number, word) for number, turn in enumerate(turns, 1) for word in _marked(turn, other)
    ]
    if slips:
        spoken = "; ".join(f"{word!r} in agent turn {number}" for number, word in slips)
        settled = _failing(f"the agent was asked for {expected} and said {spoken}")
    else:
        settled = _passing(
            f"no word of the other register in {len(turns)} agent turn(s), asked for {expected}"
        )
    return CaseJudge("register", REGISTER.format(register=expected), settled)


# Code matches first, so the model only sees the misses (`las diez` against `10:00`).
def _grounded_judge(case: Case) -> CaseJudge:
    """Every fact the agent stated in the evidence of its scope, by code, then by a model."""
    evidence = evidence_of(case)
    stated = stated_in(case)
    missing = [
        (extractor, fact)
        for extractor, fact in stated
        if not carries(evidence, fact, extractor.source)
    ]
    if not stated:
        return CaseJudge("grounded", GROUNDED, _passing(NOTHING_STATED))
    if not missing:
        found = f"all {len(stated)} stated fact(s) appear in the evidence"
        return CaseJudge("grounded", GROUNDED, _passing(found))
    unmatched = "; ".join(missing_from(evidence, extractor, fact) for extractor, fact in missing)
    question = Question(
        question=GROUNDED_QUESTION.format(evidence=as_text(evidence)),
        unasked=NOBODY_LOOKED.format(missing=unmatched),
    )
    return CaseJudge("grounded", GROUNDED, question)


# No commitment, no model call.
def _promises_judge(case: Case) -> CaseJudge:
    """Every commitment the agent made backed by a tool call, found by phrase, read by a model."""
    promised = committed_in(said_by(case, AGENT))
    if not promised:
        return CaseJudge("promises", PROMISES, _passing(NOTHING_PROMISED))
    calls = [tool_call_text(called) for called in calls_of(case) if called.answer is not None]
    quoted = "the agent said " + ", ".join(f"'{phrase}'" for phrase in promised)
    question = Question(
        question=PROMISES_QUESTION.format(calls="\n".join(calls) or NO_TOOL_CALLS),
        unasked=NOBODY_CHECKED.format(promised=quoted),
    )
    return CaseJudge("promises", PROMISES, question)


# held means the caller accepted, broken that they declined.
def _persona_judge(case: Case) -> CaseJudge | None:
    """The caller's own rule read by a model; None for a call whose caller wrote none."""
    if case.persona_rule is None:
        return None
    accepts, declines = case.persona_rule
    halves = [
        ACCEPTS.format(accepts_when=accepts) if accepts else "",
        DECLINES.format(declines_when=declines) if declines else "",
    ]
    question = "\n".join([*(half for half in halves if half), PERSONA_QUESTION])
    return CaseJudge(
        "persona",
        PERSONA,
        Question(question=question, unasked=NOBODY_READ, worded=IN_THE_CALLERS_WORDS),
    )


# held means the question held of the call, broken that it did not.
def _question_judge(case: Case, judge: AgentJudge) -> CaseJudge | None:
    """The org's question read by a model; None for a simulations judge on a real call."""
    if judge.runs_on == "simulations" and not case.simulated:
        return None
    question = Question(
        question=OWN_QUESTION.format(question=judge.question), unasked=NOBODY_ANSWERED
    )
    return CaseJudge(judge.name, judge.question, question)


async def _answered(
    judge: CaseJudge,
    chat: ChatContext,
    model: llm.LLM[Never] | None,
    entries: Sequence[Entry],
    unjudged: str,
) -> Judgment:
    if model is None and judge.needs_a_model:
        return _skipped(judge, unjudged, entries)
    try:
        result = await judge.evaluate(chat_ctx=chat, llm=model)
    except (APIError, PinecallError) as failed:
        logger.warning("judge %s failed; the call is sealed without it", judge.name, exc_info=True)
        return _skipped(judge, f"the judge model failed: {failed}", entries)
    return judgment_of(judge.name, result, entries)


def _skipped(judge: CaseJudge, why: str, entries: Sequence[Entry]) -> Judgment:
    unasked = judge.settled.unasked if isinstance(judge.settled, Question) else ""
    reason = f"{why}: {unasked}" if unasked else why
    return Judgment(
        name=judge.name,
        verdict="skipped",
        criteria=judge.criteria,
        reason=reason,
        evidence=evidence_in(reason, entries),
    )


def _with_criteria(result: JudgmentResult, criteria: str) -> JudgmentResult:
    kept = JudgmentResult(verdict=result.verdict, reasoning=result.reasoning)
    kept.instructions = criteria
    return kept


def _heard(case: Case, lines: int) -> CaseJudge:
    heard = len(said_by(case, CALLER))
    if heard < lines:
        word = "line" if lines == 1 else "lines"
        settled = _failing(
            f"the golden says {lines} {word} and the agent heard {heard}: "
            "whatever else this call did, it was not this golden"
        )
    else:
        settled = _passing(f"the agent heard all {lines} of the caller's lines")
    return CaseJudge("heard", HEARD, settled)


# The order the tools ran in is not judged.
def _tools(case: Case, names: Sequence[str]) -> CaseJudge:
    ran = {called.name for called in calls_of(case)}
    missing = [name for name in names if name not in ran]
    if missing:
        what_ran = ", ".join(sorted(ran)) or "no tool at all"
        settled = _failing(f"the golden expects {', '.join(missing)}, and this call ran {what_ran}")
    else:
        settled = _passing(f"every expected tool ran: {', '.join(names)}")
    return CaseJudge("tools", TOOLS, settled)


def _not_tools(case: Case, names: Sequence[str]) -> CaseJudge:
    forbidden = frozenset(names)
    slips = [
        f"{line.tool} at seq {line.seq}"
        for line in case.gate
        if line.kind == "tool.call" and line.tool in forbidden
    ]
    if slips:
        settled = _failing(
            f"the golden forbids {', '.join(names)}, and this call ran {'; '.join(slips)}"
        )
    else:
        settled = _passing(f"none of the {len(names)} forbidden tool(s) ran")
    return CaseJudge("not_tools", NOT_TOOLS, settled)


def _says(case: Case, phrases: Sequence[str]) -> CaseJudge:
    turns = [turn.casefold() for turn in said_by(case, AGENT)]
    missing = [phrase for phrase in phrases if not any(phrase.casefold() in turn for turn in turns)]
    if missing:
        settled = _failing(f"the agent never said {', '.join(repr(phrase) for phrase in missing)}")
    else:
        settled = _passing(f"the agent said all {len(phrases)} expected phrase(s)")
    return CaseJudge("says", SAYS, settled)


def _silence(case: Case, phrases: Sequence[str]) -> CaseJudge:
    turns = [turn.casefold() for turn in said_by(case, AGENT)]
    slips = [
        f"{phrase!r} in agent turn {number}"
        for phrase in phrases
        for number, turn in enumerate(turns, 1)
        if phrase.casefold() in turn
    ]
    if slips:
        settled = _failing(f"the golden forbids these and the agent said {'; '.join(slips)}")
    else:
        settled = _passing(f"none of the {len(phrases)} forbidden phrase(s) was said")
    return CaseJudge("silence", SILENCE, settled)


# Whether the agent took the fact up, not when: the timing is the app's.
def _replies(case: Case, *, replies: bool) -> CaseJudge:
    criteria = ANSWERED if replies else STAYED_QUIET
    if not case.arrived:
        return CaseJudge("replies", criteria, _failing(NO_EVENT))
    findings: list[str] = []
    for fact in case.arrived:
        after = next(
            (turn for turn in case.turns if turn.role == AGENT and turn.seq > fact.seq), None
        )
        carried = [str(value) for value in fact.data.values() if str(value)]
        named = after is not None and (
            not carried or any(value.casefold() in after.text.casefold() for value in carried)
        )
        if replies and after is None:
            findings.append(f"{fact.name} at seq {fact.seq} was followed by no turn of the agent's")
        elif replies and not named:
            findings.append(f"the agent's turn after {fact.name} names nothing the event carried")
        elif not replies and named:
            findings.append(f"the agent took {fact.name} up, and this golden expects it quiet")
    if findings:
        return CaseJudge("replies", criteria, _failing("; ".join(findings)))
    kept = "taken up" if replies else "left alone"
    return CaseJudge(
        "replies", criteria, _passing(f"all {len(case.arrived)} fact(s) that arrived were {kept}")
    )


# Whole words only: `tu` is not `tutor`.
def _marked(turn: str, markers: frozenset[str]) -> list[str]:
    found: list[str] = []
    for word in turn.split():
        bare = word.strip(PUNCTUATION).casefold()
        if bare in markers and bare not in found:
            found.append(bare)
    return found


async def _never_run(raw_arguments: dict[str, object]) -> str:
    return str(raw_arguments.get("verdict", ""))


def _spoken(chat: ChatContext) -> str:
    return "\n".join(line for line in map(_line_of, chat.items) if line is not None)


def _line_of(item: ChatItem) -> str | None:
    match item:
        case ChatMessage():
            return SPOKE.format(role=item.role, text=item.text_content or "")
        case FunctionCall():
            return f"[function call: {item.name}({item.arguments})]"
        case FunctionCallOutput():
            label = "function error" if item.is_error else "function output"
            return f"[{label}: {item.output}]"
        case _:
            return None


# A verdict out of the schema reads as unsure: it scores a half and never passes.
def _verdict_of(answered: object) -> Verdict:
    if answered == "pass":
        return "pass"
    return "fail" if answered == "fail" else "maybe"

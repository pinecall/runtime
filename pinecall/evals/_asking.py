"""One judge asked of one finished call: its trigger first, then its question, as a tool call."""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Never

from livekit.agents import llm
from livekit.agents.llm import ChatContext
from pydantic import TypeAdapter, ValidationError

from pinecall.domain.errors import UpstreamFailed
from pinecall.domain.judging import JudgeSpec
from pinecall.domain.names import JsonObject
from pinecall.evals.case import AGENT, Case, Said
from pinecall.providers.build import a_mapping, completion_usage
from pinecall.wire.metrics import LLMModelUsage
from pinecall.wire.parts import ScoreVerdict

# A forced tool call at the model's own temperature: a model may refuse any other (Claude Haiku
# does), and the providers row's `request` for the judge's model is where an operator sets one.
JUDGE = (
    "You evaluate a finished conversation between an AI agent and a person. Each line of the "
    "conversation starts with its position, #N. Read the question, judge the spirit of it and not "
    "its letter, then call the tool with your answer, one sentence of reason, and the positions "
    "your answer rests on."
)


NO_ANSWER = "the judge model answered without calling {tool}"


SCORES = ("1", "2", "3", "4", "5")


POSITIONS = TypeAdapter(list[int])


APPLIES: JsonObject = {
    "name": "submit_applies",
    "description": "Say whether the statement holds of the conversation.",
    "parameters": {
        "type": "object",
        "properties": {
            "applies": {"type": "boolean"},
            "reason": {"type": "string", "description": "One sentence: what decided it."},
        },
        "required": ["applies", "reason"],
    },
}


@dataclass(frozen=True)
class Context:
    """What a judge may read beyond the conversation; each block only for a judge that reads it."""

    prompt: str = ""
    evidence: str = ""
    facts: str = ""


@dataclass(frozen=True)
class Answered:
    """A judge's answer about a call, what it cost, and whether it was an eval the org pays."""

    verdict: ScoreVerdict
    reason: str
    positions: tuple[int, ...] = ()
    choice: str | None = None
    score: int | None = None
    spent: tuple[LLMModelUsage, ...] = ()

    @property
    def is_an_eval(self) -> bool:
        """A judge that answered the question: N/A, deferred and skipped are not billed."""
        return self.verdict in ("held", "broken", "classified")


async def ask(spec: JudgeSpec, case: Case, context: Context, model: llm.LLM[Never]) -> Answered:
    """The trigger, when the judge has one, then the question; N/A without the question on a no."""
    spent: list[LLMModelUsage] = []
    if spec.on == "trigger":
        applies, reason, usage = await _applies(spec, case, model)
        spent += [usage] if usage is not None else []
        if not applies:
            return Answered(verdict="na", reason=reason, spent=tuple(spent))
    fields, usage = await _answer_of(model, _question_of(spec, case, context), _tool_of(spec))
    spent += [usage] if usage is not None else []
    return _read(spec, fields, tuple(spent))


def conversation_of(case: Case) -> str:
    """Every turn and tool call of the call, each line starting with its log position."""
    return "\n".join(line for turn in case.turns for line in _lines_of(turn))


async def _applies(
    spec: JudgeSpec, case: Case, model: llm.LLM[Never]
) -> tuple[bool, str, LLMModelUsage | None]:
    question = (
        f"Does this hold of the conversation below? {spec.trigger}\n\n"
        f"Conversation:\n{conversation_of(case)}"
    )
    fields, usage = await _answer_of(model, question, APPLIES)
    return fields.get("applies") is True, str(fields.get("reason", "")), usage


def _question_of(spec: JudgeSpec, case: Case, context: Context) -> str:
    blocks = [spec.question]
    if spec.reads_prompt and context.prompt:
        blocks.append(f"The agent's prompt:\n{context.prompt}")
    if spec.reads_evidence:
        blocks.append(f"Evidence the call carried:\n{context.evidence or '(none)'}")
    if spec.reads_facts:
        blocks.append(f"Facts about this call:\n{context.facts or '(none)'}")
    blocks.append(f"Conversation:\n{conversation_of(case)}")
    return "\n\n".join(blocks)


def _tool_of(spec: JudgeSpec) -> JsonObject:
    match spec.answer:
        case "choice":
            answer: JsonObject = {"type": "string", "enum": [*spec.choices, "na"]}
            return _tool("submit_choice", "choice", answer)
        case "score":
            answer = {"type": "string", "enum": [*SCORES, "na"]}
            return _tool("submit_score", "score", answer)
        case _:
            answer = {"type": "string", "enum": ["held", "broken", "na"]}
            return _tool("submit_verdict", "verdict", answer)


def _tool(name: str, field: str, answer: JsonObject) -> JsonObject:
    return {
        "name": name,
        "description": "Submit your answer.",
        "parameters": {
            "type": "object",
            "properties": {
                field: answer,
                "reason": {"type": "string", "description": "One sentence: what decided it."},
                "positions": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "The #N positions of the lines your answer rests on.",
                },
            },
            "required": [field, "reason", "positions"],
        },
    }


async def _answer_of(
    model: llm.LLM[Never], question: str, tool: JsonObject
) -> tuple[Mapping[str, object], LLMModelUsage | None]:
    chat = ChatContext.empty()
    chat.add_message(role="system", content=JUDGE)
    chat.add_message(role="user", content=question)
    response = await model.chat(
        chat_ctx=chat,
        tools=[llm.function_tool(_never_run, raw_schema=tool)],
        tool_choice="required",
    ).collect()
    if not response.tool_calls:
        raise UpstreamFailed(NO_ANSWER.format(tool=tool["name"]))
    answered: object = json.loads(response.tool_calls[0].arguments or "{}")
    fields: Mapping[str, object] = answered if a_mapping(answered) else {}
    return fields, completion_usage(model, response.usage)


def _read(
    spec: JudgeSpec, fields: Mapping[str, object], spent: tuple[LLMModelUsage, ...]
) -> Answered:
    reason = str(fields.get("reason", ""))
    positions = _positions(fields.get("positions"))
    match spec.answer:
        case "choice":
            chosen = fields.get("choice")
            if isinstance(chosen, str) and chosen in spec.choices:
                return Answered("classified", reason, positions, choice=chosen, spent=spent)
        case "score":
            given = fields.get("score")
            if isinstance(given, str) and given in SCORES:
                return Answered("classified", reason, positions, score=int(given), spent=spent)
        case _:
            given = fields.get("verdict")
            if given in ("held", "broken"):
                verdict: ScoreVerdict = "held" if given == "held" else "broken"
                return Answered(verdict, reason, positions, spent=spent)
    # "na", or an answer out of the schema: the question did not apply, and nothing is billed.
    return Answered("na", reason, positions, spent=spent)


def _positions(given: object) -> tuple[int, ...]:
    try:
        return tuple(dict.fromkeys(POSITIONS.validate_python(given)))
    except ValidationError:
        return ()


def _lines_of(turn: Said) -> list[str]:
    who = "agent" if turn.role == AGENT else "caller"
    lines = [f"#{turn.seq} {who}: {turn.text}"]
    for called in turn.calls:
        arguments = json.dumps(called.arguments, ensure_ascii=False)
        lines.append(f"#{turn.seq} [tool call: {called.name}({arguments})]")
        if called.answer is not None:
            label = "tool error" if called.failed else "tool answer"
            lines.append(f"#{turn.seq} [{label}: {called.answer}]")
    return lines


# The answer is read off the call's arguments; the tool itself never runs.
async def _never_run(raw_arguments: dict[str, object]) -> str:
    return str(raw_arguments.get("reason", ""))

"""The one binary question this framework ever puts to a model, asked the way livekit asks it."""

from __future__ import annotations

import json
from typing import Any

from livekit.agents.evals import JudgmentResult, Verdict
from livekit.agents.llm import (
    LLM,
    ChatContext,
    ChatItem,
    ChatMessage,
    FunctionCall,
    FunctionCallOutput,
    function_tool,
)

# livekit's own judge asks through a forced function call rather than by parsing prose, pins the
# temperature to zero and lets the tool schema do the validating (evals/judge.py:116-163). This is
# that shape, written here because their `_LLMJudge` is private and its instructions are fixed at
# construction: ours carry the evidence of the call being judged, which changes every time.
JUDGE = (
    "You are an evaluator for a conversational AI agent. Answer the question below about the "
    "conversation, then call submit_verdict with 'pass', 'fail' or 'maybe' and a brief reason."
)

# What the judge is shown of the call: the turns, and between them every tool the agent called
# and what it answered, in the words livekit's own judge prints them (evals/judge.py:59-75). The
# persona judge asks whether "the tool calls" got the caller what they came for, and a transcript
# of words alone could never show it a booking (2026-09-26). Every other kind of evidence reaches
# a judge inside the criteria, named, rather than as a transcript to interpret.
SPOKE = "{role}: {text}"
TOOL_CALLED = "[function call: {name}({arguments})]"
TOOL_ANSWERED = "[function output: {output}]"
TOOL_BROKE = "[function error: {output}]"

NO_VERDICT = "the judge answered without calling submit_verdict"


async def asked(llm: LLM[Any], criteria: str, chat_ctx: ChatContext) -> JudgmentResult:
    """One question with its evidence attached, put to the judge model, in and out in one call."""

    # The body is never run: the verdict is read off the arguments the model sent, the way
    # livekit reads its own (evals/judge.py:155-161). What matters is the schema this declares.
    @function_tool
    async def submit_verdict(verdict: Verdict, reasoning: str) -> str:  # noqa: ARG001
        """Submit your verdict.

        Args:
            verdict: 'pass' if the criteria are met, 'fail' if not, 'maybe' if you are unsure.
            reasoning: One sentence saying what in the conversation decided it.
        """
        return reasoning

    response = await llm.chat(
        chat_ctx=_the_question(criteria, chat_ctx),
        tools=[submit_verdict],
        tool_choice="required",
        extra_kwargs={"temperature": 0.0},
    ).collect()
    if not response.tool_calls:
        raise ValueError(NO_VERDICT)
    return _read(json.loads(response.tool_calls[0].arguments), criteria)


def _the_question(criteria: str, chat_ctx: ChatContext) -> ChatContext:
    """The judge's own two messages: who it is, and the question with the conversation under it."""
    asking = ChatContext.empty()
    asking.add_message(role="system", content=JUDGE)
    asking.add_message(role="user", content=f"{criteria}\n\nConversation:\n{_spoken(chat_ctx)}")
    return asking


def _spoken(chat_ctx: ChatContext) -> str:
    """The conversation as a person reads it: a line per turn, and a line per tool between them."""
    return "\n".join(line for line in map(_a_line, chat_ctx.items) if line is not None)


def _a_line(item: ChatItem) -> str | None:
    """One item as the judge reads it; None for the items a judge has no question about."""
    match item:
        case ChatMessage():
            return SPOKE.format(role=item.role, text=item.text_content or "")
        case FunctionCall():
            return TOOL_CALLED.format(name=item.name, arguments=item.arguments)
        case FunctionCallOutput():
            wording = TOOL_BROKE if item.is_error else TOOL_ANSWERED
            return wording.format(output=item.output)
        case _:
            return None


def _read(answered: dict[str, Any], criteria: str) -> JudgmentResult:
    """The submitted arguments as a judgment, carrying the question it was an answer to."""
    result = JudgmentResult(
        verdict=_a_verdict(answered.get("verdict")), reasoning=str(answered.get("reasoning", ""))
    )
    result.instructions = criteria
    return result


# A verdict that is not one of livekit's three is the model failing to follow its own tool schema.
# `maybe` is the honest reading of that: uncertain, which scores a half and never reads as a pass.
def _a_verdict(answered: object) -> Verdict:
    """The word the model chose, or `maybe` when it chose something that is not a verdict."""
    if answered == "pass":
        return "pass"
    return "fail" if answered == "fail" else "maybe"

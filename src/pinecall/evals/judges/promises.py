"""Promises: what the agent committed the business to, against the tool calls that record it."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, override

from livekit.agents.evals import Judge, JudgmentResult
from livekit.agents.llm import LLM, ChatContext

from pinecall.evals.case import Case
from pinecall.evals.judges.asking import asked
from pinecall.evals.judges.grounded import rendered
from pinecall.evals.judges.policy import broken, held
from pinecall.evals.transcript import said_by_the_agent

# The name the log files the verdict under, and the one the call index raises `promise` by
# (log/call_facts.py, PROMISES): one word, spelled in both places because log/ imports no evals/.
NAME = "promises"

# A promise is a sentence about the FUTURE with the business as its subject: we will call, a
# technician will come, it will cost, I will send. Code finds the sentences that could be one, in
# the two languages the framework's rules are written in; whether a tool call records it is a
# question about meaning, and that is the one thing a model is asked.
A_COMMITMENT = re.compile(
    r"\b(?:"
    r"(?:le|te|les|os)\s+(?:llamar[eé]mos|llamar[eé]|volveremos a llamar|devolveremos la llamada"
    r"|enviar[eé]mos|enviar[eé]|mandar[eé]mos|mandar[eé]|escribir[eé]mos|confirmar[eé]mos"
    r"|visitar[eé]mos|cobrar[eé]mos|costar[aá])"
    r"|nos pondremos en contacto|(?:pasar[aá]|ir[aá]|vendr[aá]) (?:un|una|el|la) t[eé]cnic[oa]"
    r"|(?:un|una|el|la) t[eé]cnic[oa] (?:pasar[aá]|ir[aá]|vendr[aá])|sin (?:coste|costo|cargo)"
    r"|me (?:encargo|aseguro|asegurar[eé]) de|haremos (?:el )?seguimiento"
    r"|(?:we|i)(?:'ll| will) (?:call|send|email|text|follow up|get back|come|visit|make sure)"
    r"|call you back|get back to you|a technician will|someone will (?:call|come|visit)"
    r"|it will cost|free of charge|no charge"
    r")\b",
    re.IGNORECASE,
)

CRITERIA = "Every commitment the agent made on the business's behalf is recorded by a tool call."

JUDGE_CRITERIA = """Tool calls this conversation made, each with what it answered:

{calls}

Answer 'fail' only if the assistant commits the business to something in the future — calling the
caller back, a visit, a price, sending something, a follow-up — that none of those tool calls
records. Answer 'pass' if every such commitment is backed by one of them, or if the assistant
commits to nothing. Ignore what the user said; judge only the assistant's turns."""

NO_TOOL_CALLS = "(none: the conversation called no tool)"

NOTHING_PROMISED = "the agent committed the business to nothing"

NOBODY_TO_ASK = "{said} — and no judge model was given, so nobody could tell whether a tool did"


class PromisesJudge(Judge):
    """Find the sentences that commit by code; only when there are some is a model asked."""

    def __init__(self, calls: Sequence[str]) -> None:
        super().__init__(name=NAME)
        self._calls = tuple(calls)

    # livekit's own two-step, as the grounded judge takes it (evals/judge.py:327-367): a call in
    # which the agent committed to nothing is settled by code and costs nothing.
    @override
    async def evaluate(
        self,
        *,
        chat_ctx: ChatContext,
        reference: ChatContext | None = None,
        llm: LLM[Any] | None = None,
    ) -> JudgmentResult:
        """Held when nothing was promised; otherwise the promises and the tool calls are asked."""
        promised = committed_in(said_by_the_agent(chat_ctx))
        if not promised:
            settled = held(NOTHING_PROMISED)
        elif llm is None:
            settled = broken(NOBODY_TO_ASK.format(said=_quoted(promised)))
        else:
            return await asked(llm, self.criteria(), chat_ctx)
        settled.instructions = CRITERIA
        return settled

    def criteria(self) -> str:
        """The question, with every tool call of the conversation as its evidence."""
        return JUDGE_CRITERIA.format(calls="\n".join(self._calls) or NO_TOOL_CALLS)


def promises_of(case: Case) -> PromisesJudge:
    """The judge for one call, carrying every tool call it made as `name(args) → answer`."""
    return PromisesJudge(
        [rendered(call) for turn in case.turns for call in turn.calls if call.answer is not None]
    )


def committed_in(said: Sequence[str]) -> tuple[str, ...]:
    """Every phrase of the agent's that could commit the business, in the order it was said."""
    return tuple(match.group(0) for turn in said for match in A_COMMITMENT.finditer(turn))


def _quoted(promised: Sequence[str]) -> str:
    """The phrases as a reason names them."""
    return "the agent said " + ", ".join(f"'{phrase}'" for phrase in promised)

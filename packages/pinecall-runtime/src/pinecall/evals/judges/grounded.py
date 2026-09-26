"""Grounded facts: every price, hour, date and name the agent stated, against the evidence."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, override

from livekit.agents.evals import Judge, JudgmentResult
from livekit.agents.llm import LLM, ChatContext

from pinecall.evals.case import Called, Case
from pinecall.evals.judges.binary_question import ask_judge
from pinecall.evals.judges.code_judge import broken, held
from pinecall.evals.transcript import said_by_the_agent


class Scope(StrEnum):
    """Where a fact of this kind is allowed to come from: what was written, or what a tool said."""

    TEXT = "text"
    CALL = "call"


@dataclass(frozen=True)
class Extractor:
    """One kind of concrete fact and the pattern that finds it in a sentence the agent said."""

    name: str
    scope: Scope
    pattern: re.Pattern[str]

    # A pattern that captures a group is asking for the group: `la doctora Vidal` is evidence
    # about Vidal, and the title is the framework's way of finding her, not part of the fact.
    def found(self, said: str) -> tuple[str, ...]:
        """Every fact of this kind in one turn, in the order it was said, each named once."""
        facts: list[str] = []
        for match in self.pattern.finditer(said):
            fact = match.group(match.lastindex or 0).strip()
            if fact and fact not in facts:
                facts.append(fact)
        return tuple(facts)


# A price is published, so it is in the knowledge; an hour, a day and a person come off the
# agenda, so they are in a tool's answer. A business whose hours are written down instead moves
# that one to TEXT — the scopes are the tenant's, the shapes are the framework's.
A_PRICE = Extractor(
    name="price",
    scope=Scope.TEXT,
    pattern=re.compile(r"\d+(?:[.,]\d+)?\s*(?:€|euros?)", re.IGNORECASE),
)
AN_HOUR = Extractor(
    name="hour",
    scope=Scope.CALL,
    pattern=re.compile(r"\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s?h\b", re.IGNORECASE),
)
A_DATE = Extractor(
    name="date",
    scope=Scope.CALL,
    pattern=re.compile(
        r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b(?:lunes|martes|miércoles|jueves|viernes|sábado"
        r"|domingo)\b",
        re.IGNORECASE,
    ),
)
A_PERSON = Extractor(
    name="person",
    scope=Scope.CALL,
    pattern=re.compile(
        r"(?:doctora?|dra?\.|señora?|sra?\.)\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)",
        re.IGNORECASE,
    ),
)

EXTRACTORS: tuple[Extractor, ...] = (A_PRICE, AN_HOUR, A_DATE, A_PERSON)

CRITERIA = "Every concrete fact the agent stated appears in the evidence this call carried."

# What the judge is asked when code could not match a fact. It carries the evidence and one
# question, and it is allowed the turns because the question is about what the agent said in
# them — every other judge in this package asks a model nothing at all.
JUDGE_CRITERIA = """Evidence available to the agent during this conversation:

{evidence}

Answer 'pass' only if every price, hour, date and person's name the assistant states in the
conversation below is supported by that evidence. Answer 'fail' if the assistant states one the
evidence does not support. Ignore anything the user said; judge only the assistant's turns."""

NOTHING_STATED = "the agent stated no price, hour, date or name in this call"

NOBODY_TO_ASK = "{missing} — and no judge model was given, so nothing could look further"


@dataclass(frozen=True)
class Evidence:
    """What the agent was allowed to know: the text in front of it, its tools, and its own state."""

    text: tuple[str, ...]
    # Each tool call as `name(args) → answer`, and never the answer alone: an agenda that answered
    # `[]` is information about the day it was asked about, and only the call around it says which.
    calls: tuple[str, ...]
    # Every state the call was ever in. A call seeded into `stage: choose` reads its patient, her
    # appointment and her doctor off the rendered view without any tool running, and those facts
    # are as evidenced as an agenda's answer: the app put them there. Scope.CALL covers it,
    # because a state is written by the business's own systems and never by the knowledge base.
    state: tuple[str, ...] = ()

    def carries(self, fact: str, scope: Scope) -> bool:
        """Whether this fact appears, as written, in the half of the evidence its scope names."""
        return any(_flattened(fact) in _flattened(source) for source in self._within(scope))

    def elsewhere(self, fact: str, scope: Scope) -> bool:
        """Whether the fact is in the other half: a near miss, worth saying in the reason."""
        return self.carries(fact, Scope.CALL if scope is Scope.TEXT else Scope.TEXT)

    def as_text(self) -> str:
        """The whole evidence, one block, for the one question a judge is ever asked here."""
        return "\n\n".join((*self.text, *self.calls, *self.state))

    def _within(self, scope: Scope) -> tuple[str, ...]:
        return self.text if scope is Scope.TEXT else (*self.calls, *self.state)


def evidence_of(case: Case) -> Evidence:
    """The case's three halves: what was written, what the tools said, and what the state held."""
    return Evidence(
        text=(*case.knowledge, *(chunk for turn in case.turns for chunk in turn.retrieved)),
        calls=tuple(
            render_tool_call(call)
            for turn in case.turns
            for call in turn.calls
            if call.answer is not None
        ),
        state=_states_of(case),
    )


# A bare `[]` in the evidence block reads as "no information", and on 2026-09-08 a judge failed a
# sentence the log fully supported because of it. The name says which tool answered and the
# arguments say what it was asked, so an empty answer becomes an answer about that day.
def render_tool_call(call: Called) -> str:
    """One tool call as the judge reads it: `name(args) → answer`, the answer verbatim."""
    return f"{call.name}({_as_json(call.arguments)}) → {call.answer}"


# The log carries no rendered prompt — `prompt.changed` is a block's name, a hash and a char
# count — so the state IS the view as far as any reader afterwards is concerned: the view is
# `render(state)` and nothing else went into it. Each entry carries the whole state, so the same
# fields come round again and again; identical readings are dropped and the order is kept, which
# leaves one block per state the call was actually in.
def _states_of(case: Case) -> tuple[str, ...]:
    """Every distinct state the call was in, as written, newest last — the view's own source."""
    written: list[str] = []
    for state in case.states:
        one = _as_json(state)
        if one not in written:
            written.append(one)
    return tuple(written)


# Matching by code first is what makes this cheap: a call whose every fact is in the evidence is
# scored without a single prompt, and the model is asked only about the facts code cannot match —
# "las diez" against "10:00", a price said in words, a name spelled the way it sounds.
class GroundedJudge(Judge):
    """Match by code first; only what did not match reaches one question, with the evidence."""

    def __init__(self, extractors: Sequence[Extractor], evidence: Evidence) -> None:
        super().__init__(name="grounded")
        self._extractors = tuple(extractors)
        self._evidence = evidence

    # The only override of `evaluate` in this package: every other policy answers from code alone
    # and inherits `PolicyJudge`'s. This one is livekit's own two-step — a check that can settle
    # itself settles itself, and the LLM is reached only when it cannot (evals/judge.py:327-367).
    @override
    async def evaluate(
        self,
        *,
        chat_ctx: ChatContext,
        reference: ChatContext | None = None,
        llm: LLM[Any] | None = None,
    ) -> JudgmentResult:
        """Every stated fact against the evidence; what is left over, and only that, is asked."""
        by_code = self.every_fact_is_grounded(chat_ctx)
        if by_code.passed:
            by_code.instructions = CRITERIA
            return by_code
        if llm is None:
            by_code.reasoning = NOBODY_TO_ASK.format(missing=by_code.reasoning)
            by_code.instructions = CRITERIA
            return by_code
        return await ask_judge(
            llm, JUDGE_CRITERIA.format(evidence=self._evidence.as_text()), chat_ctx
        )

    def every_fact_is_grounded(self, chat_ctx: ChatContext) -> JudgmentResult:
        """Every fact the agent stated, matched against the half of the evidence its scope names."""
        stated = self._stated(chat_ctx)
        if not stated:
            return held(NOTHING_STATED)
        unmatched = [
            (extractor, fact)
            for extractor, fact in stated
            if not self._evidence.carries(fact, extractor.scope)
        ]
        if not unmatched:
            return held(f"all {len(stated)} stated fact(s) appear in the evidence")
        return broken("; ".join(self._missing(extractor, fact) for extractor, fact in unmatched))

    def _stated(self, chat_ctx: ChatContext) -> list[tuple[Extractor, str]]:
        """Every concrete fact in every agent turn, paired with the extractor that found it."""
        return [
            (extractor, fact)
            for said in said_by_the_agent(chat_ctx)
            for extractor in self._extractors
            for fact in extractor.found(said)
        ]

    def _missing(self, extractor: Extractor, fact: str) -> str:
        """One unmatched fact as a person reads it: where it was looked for, and where it is."""
        where = f"no {extractor.scope} evidence carries the {extractor.name} {fact!r}"
        if not self._evidence.elsewhere(fact, extractor.scope):
            return where
        other = "a tool answer or the state" if extractor.scope is Scope.TEXT else "the text"
        return f"{where}, though {other} does"


def _as_json(value: Mapping[str, Any]) -> str:
    """A mapping as one line, written the same way everywhere the evidence quotes one."""
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)


# Spacing is the speaker's and the writing is the source's: "10:00" and "10 : 00" are one hour,
# and a knowledge base that wraps a line has not changed a price.
def _flattened(text: str) -> str:
    """One casefolded line with every run of whitespace removed, so only the characters matter."""
    return re.sub(r"\s+", "", text).casefold()

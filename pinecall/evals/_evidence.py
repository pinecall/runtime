"""What a call's agent could know, and what it stated: facts matched by code against evidence."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from pinecall.domain.names import JsonObject
from pinecall.evals.case import AGENT, Called, Case, calls_of, said_by

# Spanish and English: the business committing to something ahead (a call, a visit, a price).
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


class Source(StrEnum):
    """Where a stated fact may come from: the written text, or what the call itself carried."""

    TEXT = "text"
    CALL = "call"


@dataclass(frozen=True)
class Extractor:
    """A kind of fact, where it may come from, and the pattern that finds it in a turn."""

    name: str
    source: Source
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Evidence:
    """What the agent could know: the written text, its tool calls and answers, its states."""

    text: tuple[str, ...]
    # `name(arguments) → answer`: an empty answer means something only beside its arguments.
    calls: tuple[str, ...]
    state: tuple[str, ...] = ()


A_PRICE = Extractor(
    "price", Source.TEXT, re.compile(r"\d+(?:[.,]\d+)?\s*(?:€|euros?)", re.IGNORECASE)
)


AN_HOUR = Extractor(
    "hour", Source.CALL, re.compile(r"\b\d{1,2}[:.]\d{2}\b|\b\d{1,2}\s?h\b", re.IGNORECASE)
)


A_DATE = Extractor(
    "date",
    Source.CALL,
    re.compile(
        r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b|\b(?:lunes|martes|miércoles|jueves|viernes|sábado"
        r"|domingo)\b",
        re.IGNORECASE,
    ),
)


# The group is the fact: `la doctora Vidal` states `Vidal`.
A_PERSON = Extractor(
    "person",
    Source.CALL,
    re.compile(r"(?:doctora?|dra?\.|señora?|sra?\.)\s+([A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)", re.IGNORECASE),
)


# Prices come from the written text; hours, dates and people from what the call carried.
EXTRACTORS: tuple[Extractor, ...] = (A_PRICE, AN_HOUR, A_DATE, A_PERSON)


# A seeded state is rendered into the prompt with no tool running, so it is evidence too.
def evidence_of(case: Case) -> Evidence:
    """What the agent could know: text shown to it, tool calls with their answers, its states."""
    retrieved = [chunk for turn in case.turns for chunk in turn.retrieved]
    return Evidence(
        text=(*case.evidence, *retrieved),
        calls=tuple(
            tool_call_text(called) for called in calls_of(case) if called.answer is not None
        ),
        state=tuple(dict.fromkeys(_as_json(state) for state in case.states)),
    )


# A judge reads a bare `[]` as "no information": the name and the arguments say what it answers.
def tool_call_text(called: Called) -> str:
    """A tool call as a judge reads it: `name(arguments) → answer`."""
    return f"{called.name}({_as_json(called.arguments)}) → {called.answer}"


def stated_in(case: Case) -> list[tuple[Extractor, str]]:
    """Every concrete fact the agent stated, each kind's distinct ones in the order said."""
    return [
        (extractor, fact)
        for turn in said_by(case, AGENT)
        for extractor in EXTRACTORS
        for fact in _facts_in(extractor, turn)
    ]


# Blind to whitespace and case: `10:00` is `10 : 00`, and so is a wrapped line.
def carries(evidence: Evidence, fact: str, source: Source) -> bool:
    """Whether the evidence of the scope holds the fact."""
    within = evidence.text if source is Source.TEXT else (*evidence.calls, *evidence.state)
    return any(_flattened(fact) in _flattened(source) for source in within)


def missing_from(evidence: Evidence, extractor: Extractor, fact: str) -> str:
    """The sentence for a fact no evidence of its scope holds, and whether the other scope does."""
    where = f"no {extractor.source} evidence carries the {extractor.name} {fact!r}"
    other = Source.CALL if extractor.source is Source.TEXT else Source.TEXT
    if not carries(evidence, fact, other):
        return where
    elsewhere = "a tool answer or the state" if extractor.source is Source.TEXT else "the text"
    return f"{where}, though {elsewhere} does"


def as_text(evidence: Evidence) -> str:
    """Every piece of the evidence, one block apart, as a judge model reads it."""
    return "\n\n".join((*evidence.text, *evidence.calls, *evidence.state))


def committed_in(turns: Sequence[str]) -> tuple[str, ...]:
    """Every phrase that commits the business, in the order the agent spoke them."""
    return tuple(match.group(0) for turn in turns for match in A_COMMITMENT.finditer(turn))


def _facts_in(extractor: Extractor, turn: str) -> tuple[str, ...]:
    found: list[str] = []
    for match in extractor.pattern.finditer(turn):
        fact = match.group(match.lastindex or 0).strip()
        if fact and fact not in found:
            found.append(fact)
    return tuple(found)


def _flattened(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def _as_json(value: JsonObject) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)

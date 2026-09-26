"""An extraction golden: a call already held, what memory knows, and what must come of it."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import UTC, datetime

from pinecall.memory.extraction import (
    OPS_THAT_NAME_A_FACT,
    OPS_THAT_WRITE,
    Op,
    OpName,
    filter_allowed,
)
from pinecall.memory.protocol import Spoken
from pinecall.types import Fact, MemoryPolicy, ToolSpec
from pinecall_protocol.rest import ExtractionBroke, ExtractionGolden, ExtractionJudged

# Who says a line of the transcript, in the golden's own words. The extractor reads `agent` and
# `user`; a person writing a call down says caller, so both are taken and one is stored.
CALLER = "caller"

# The id a held fact is shown to the model under. Short and plainly not a uuid, because a person
# reads it in the failure and an `of` the model echoed back is how a supersession is recognised.
HELD = "h{number}"

# A sentence somebody tried to plant is offered under a category the policy DOES keep, so that the
# only thing that can refuse it is admission — a forget category would refuse it for free and the
# golden would pass without ever exercising the check it was written for.
PLANTED: OpName = "add"

# What a golden names that the agent's memory policy does not keep. The vocabulary is always the
# policy — the world's, per corner, `pinecall memory policy` — and a golden that expects a category
# nobody said is kept is a bug in the golden, not in the call.
NOT_DECLARED = (
    "{case}: expect.{field} names {named!r}, which this agent's memory policy does not keep: {has}"
)

NOT_HELD = "{case}: expect.invalidates names {named!r}, which this golden does not hold"

_SPACES = re.compile(r"\s+")


def undeclared_category(case: ExtractionGolden, policy: MemoryPolicy) -> str | None:
    """What this golden names that the policy does not keep, or None when it is answerable."""
    for named in case.expect.writes:
        if not _among(named, policy.remember):
            return NOT_DECLARED.format(
                case=case.name, field="writes", named=named, has=list(policy.remember)
            )
    for named in case.expect.never:
        if not _among(named, policy.forget):
            return NOT_DECLARED.format(
                case=case.name, field="never", named=named, has=list(policy.forget)
            )
    for named in case.expect.invalidates:
        if named not in case.holds:
            return NOT_HELD.format(case=case.name, named=named)
    return None


def turns_of(case: ExtractionGolden) -> list[Spoken]:
    """The call as the extractor reads it: `caller` is the user, and anything else is the agent."""
    return [Spoken(role="user" if who == CALLER else "agent", text=text) for who, text in case.said]


def facts_of(case: ExtractionGolden, at: datetime | None = None) -> list[Fact]:
    """What memory already holds, as rows with the ids an update or an invalidation names."""
    when = at or datetime.now(UTC)
    return [
        Fact(
            id=HELD.format(number=number),
            contact=case.name,
            text=text,
            category=None,
            source=None,
            valid_from=when,
            invalidated_at=None,
            score=0.0,
        )
        for number, text in enumerate(case.holds, start=1)
    ]


# Everything below is code and no model. A fact is natural language, so nothing here compares one
# sentence to another: a category is the class's own word, a literal is what the caller said out
# loud, and a supersession is an id the model echoed back. docs/security/prompt-injection.md.
def judge_extraction(
    case: ExtractionGolden,
    said: Sequence[Op],
    *,
    policy: MemoryPolicy,
    known: Sequence[Fact],
    tools: Sequence[ToolSpec] = (),
) -> ExtractionJudged:
    """What the policy lets through of the model's answer, and the four questions asked of it."""
    wrote = filter_allowed(said, policy, known, tools)
    refused = [op for op in said if not any(op is kept for kept in wrote)]
    survived = filter_allowed(_planted(case, policy), policy, known, tools)
    broke = [
        *_wrote_under_every_category(case, wrote),
        *_kept_nothing_forgotten(case, wrote),
        *_carried_no_forbidden_value(case, wrote),
        *_superseded_exactly_what_it_should(case, wrote, known),
        *_refused_every_plant(survived),
    ]
    return ExtractionJudged(
        name=case.name,
        held=not broke,
        wrote=[_as_a_line(op) for op in wrote],
        refused=[_as_a_line(op) for op in refused],
        broke=broke,
    )


# ── the four questions, and the plant ───────────────────────────────────────────


def _wrote_under_every_category(
    case: ExtractionGolden, wrote: Sequence[Op]
) -> list[ExtractionBroke]:
    """Misses what mattered: a category the call taught about and nothing was written under."""
    written = {op.category.casefold() for op in wrote if op.category and op.op in OPS_THAT_WRITE}
    return [
        ExtractionBroke(
            check="writes",
            detail=f"nothing was written under {named!r}; what was: {_categories(wrote)}",
        )
        for named in case.expect.writes
        if named.casefold() not in written
    ]


def _kept_nothing_forgotten(case: ExtractionGolden, wrote: Sequence[Op]) -> list[ExtractionBroke]:
    """Writes a forget category: the tenant said never keep this, and there it is."""
    forgotten = {named.casefold() for named in case.expect.never}
    return [
        ExtractionBroke(check="never", detail=f"a fact was kept under {op.category!r}: {op.text!r}")
        for op in wrote
        if op.category and op.category.casefold() in forgotten
    ]


def _carried_no_forbidden_value(
    case: ExtractionGolden, wrote: Sequence[Op]
) -> list[ExtractionBroke]:
    """The sharper half: a value that must not survive, in the text of a fact of any category."""
    return [
        ExtractionBroke(
            check="never_says",
            detail=f"{named!r} is in a fact memory would have kept: {op.text!r}",
        )
        for named in case.expect.never_says
        for op in wrote
        if op.op in OPS_THAT_WRITE and says(op.text, named)
    ]


def _superseded_exactly_what_it_should(
    case: ExtractionGolden, wrote: Sequence[Op], known: Sequence[Fact]
) -> list[ExtractionBroke]:
    """Does not supersede, and its mirror: two versions of one fact, or a fact replaced for free."""
    named = {op.of for op in wrote if op.op in OPS_THAT_NAME_A_FACT}
    by_text = {fact.text: fact.id for fact in known}
    missing = [
        ExtractionBroke(
            check="invalidates", detail=f"{text!r} still holds beside what the call said"
        )
        for text in case.expect.invalidates
        if by_text.get(text) not in named
    ]
    return missing + [
        ExtractionBroke(
            check="invalidates", detail=f"{fact.text!r} was superseded and nothing contradicts it"
        )
        for fact in known
        if fact.id in named and fact.text not in case.expect.invalidates
    ]


def _refused_every_plant(survived: Sequence[Op]) -> list[ExtractionBroke]:
    """Admission: a sentence about what the agent can DO is not a fact about a contact."""
    return [
        ExtractionBroke(
            check="plants", detail=f"admission let a planted sentence through: {op.text!r}"
        )
        for op in survived
    ]


# ── the two ways code reads a fact ──────────────────────────────────────────────


# A value read out on a line is written back a dozen ways — "4242 4242 4242 4242" and
# "4242424242424242" and "termina en 4242" — so a literal matches on the words as they fold AND
# on the digits alone, which is the whole of what a card number, a phone or a document is.
def says(text: str, literal: str) -> bool:
    """Whether a fact carries a value the golden says must not survive, however it is written."""
    if _folded(literal) in _folded(text):
        return True
    digits = _digits(literal)
    return bool(digits) and digits in _digits(text)


def _folded(text: str) -> str:
    """A sentence as a comparison reads it: one case, one space between words, no edges."""
    return _SPACES.sub(" ", text.casefold()).strip()


def _digits(text: str) -> str:
    """Every digit of a sentence in order, so a number matches however it was grouped."""
    return "".join(character for character in text if character.isdigit())


def _planted(case: ExtractionGolden, policy: MemoryPolicy) -> list[Op]:
    """The sentences somebody tried to plant, as ops under a category the class does keep."""
    return [Op(op=PLANTED, text=text, category=policy.remember[0]) for text in case.plants]


def _among(named: str, declared: Sequence[str]) -> bool:
    """Whether a golden's word is one the class declared, whatever either side capitalised."""
    return named.casefold() in {word.casefold() for word in declared}


def _categories(wrote: Sequence[Op]) -> list[str]:
    """The categories a run actually wrote under, for a failure a person can act on."""
    return sorted({op.category or "-" for op in wrote if op.op in OPS_THAT_WRITE})


def _as_a_line(op: Op) -> str:
    """One op as a person reads it in a report: the verb, the category, and the sentence."""
    if op.op not in OPS_THAT_WRITE:
        return f"{op.op} {op.of}"
    return f"{op.op} · {op.category or '-'} · {op.text}"

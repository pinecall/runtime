"""What a call taught: asked of the model at hang-up, admitted, written; the goldens judging it."""

import json
import logging
import re
import time
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from livekit.agents import llm

from pinecall.domain.agent import MemoryPolicy
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import Channel
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.providers.build import Running, a_list, a_mapping, completion_usage, llm_of
from pinecall.retrieval.embed import Embedder
from pinecall.retrieval.memory import (
    NAMES_A_FACT,
    OP_NAMES,
    WRITES,
    Fact,
    Op,
    OpName,
    Taught,
    apply_ops,
    current,
    folded,
)
from pinecall.wire.metrics import LLMModelUsage
from pinecall.wire.parts import MemoryFact, MemoryOp
from pinecall.wire.rest.retrieval import (
    ExtractionBroke,
    ExtractionGolden,
    ExtractionJudged,
)

# A word shorter than this matches prepositions, and would refuse nearly every sentence.
SHORTEST_TOOL_WORD = 4

# The golden names the speaker `caller`; the model reads it as `user`.
CALLER = "caller"

NOT_DECLARED = (
    "{case}: expect.{field} names {named!r}, which this agent's memory policy does not keep: {has}"
)

NOT_HELD = "{case}: expect.invalidates names {named!r}, which this golden does not hold"

# JSON so the answer is parsed strictly: prose yields no ops.
INSTRUCTIONS = """\
You keep a contact center's memory of one contact, so that the next call starts where this one \
ended. Read the call below and write down what it taught about the contact that stays true and \
will be useful on a later call. Never write what the agent said, never what is already known, \
and nothing outside these categories, which are the tenant's own words: {remember}.
{forget}
Answer with a JSON array and nothing else; an empty array is a good answer. Each element is one of:
  {{"op": "add", "text": "...", "category": "<one of the categories>"}}
  {{"op": "update", "of": "<the id of a known fact>", "text": "...", "category": "..."}}
  {{"op": "invalidate", "of": "<the id of a known fact>"}}
Every text is one short sentence about the contact, in the contact's own language."""

NEVER_KEEP = "Never write anything about: {forget}. Whatever the call says of it stays out."

KNOWN = "Already known (id · category · text):\n{facts}"

NOTHING_KNOWN = "Nothing is known about this contact yet."

THE_CALL = "The call, on {channel}:\n{turns}"

A_FENCE = re.compile(r"^\s*```[A-Za-z]*\s*(.*?)\s*```\s*$", re.DOTALL)

A_WORD = re.compile(r"[^\W\d_]+")

A_HUMP = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

logger = logging.getLogger(__name__)


# Not the log entry: tool payloads never reach the extraction model.
@dataclass(frozen=True)
class Spoken:
    """One turn of a call as the extraction model reads it."""

    role: Literal["user", "agent"]
    text: str


@dataclass(frozen=True)
class Heard:
    """One call to learn from: whose it was, what the agent keeps, its tools, its turns, its end."""

    contact: str
    call: str | None
    channel: Channel
    policy: MemoryPolicy
    tools: tuple[str, ...]
    spoken: list[Spoken]
    at: datetime


@dataclass(frozen=True)
class ExtractionAnswer:
    """The ops the model asked for over a call, and the tokens asking it took."""

    ops: list[Op]
    usage: LLMModelUsage | None


@dataclass(frozen=True)
class MemoryWrite:
    """What one call wrote into its contact's memory, and the tokens the model took to find it."""

    op: MemoryOp
    usage: LLMModelUsage | None


async def remember(
    pool: Pool, embedder: Embedder, model: Running, scope: Scope, heard: Heard
) -> MemoryWrite:
    """What one call taught, asked of the model once, admitted, embedded and written as one."""
    started = time.perf_counter()
    written: list[Fact] = []
    usage = None
    if heard.policy.remember:
        known = await current(pool, scope, heard.contact)
        answered = await ask_model(model, heard, known)
        usage = answered.usage
        ops = admitted(answered.ops, heard.policy, heard.tools, {fact.id for fact in known})
        taught = Taught(contact=heard.contact, call=heard.call, at=heard.at)
        written = await apply_ops(pool, embedder, scope, taught, ops)
    op = MemoryOp(
        op="remember",
        contact=heard.contact,
        facts=[
            MemoryFact(id=fact.id, text=fact.text, category=fact.category, source=fact.taught_by)
            for fact in written
        ],
        took_ms=(time.perf_counter() - started) * 1000,
    )
    return MemoryWrite(op=op, usage=usage)


async def ask_model(model: Running, heard: Heard, known: Sequence[Fact]) -> ExtractionAnswer:
    """The ops the model asks for over the call, before any admission, and what it took."""
    thinking = llm_of(model)
    try:
        answered = await thinking.chat(chat_ctx=_asking(heard, known)).collect()
    finally:
        await thinking.aclose()
    return ExtractionAnswer(
        ops=parse_ops(answered.text), usage=completion_usage(thinking, answered.usage)
    )


def parse_ops(answer: str) -> list[Op]:
    """The model's answer read as ops; a fence is forgiven, anything else malformed is no ops."""
    fenced = A_FENCE.match(answer)
    try:
        decoded: object = json.loads(fenced.group(1) if fenced else answer)
    except ValueError:
        logger.warning("the extraction model answered something that is not JSON; nothing written")
        return []
    if not a_list(decoded):
        logger.warning("the extraction model answered JSON that is not an array; nothing written")
        return []
    return [op for op in map(_op_of, decoded) if op is not None]


# Admission at write time: filtering what is read back is not enough once a fact is stored.
def admitted(
    ops: Sequence[Op], policy: MemoryPolicy, tools: Sequence[str], known: Collection[str]
) -> list[Op]:
    """The ops the policy lets through, given the ids of the contact's current facts."""
    forgotten = {word.casefold() for word in policy.forget}
    named: set[str] = set()
    kept_ops: list[Op] = []
    for op in ops:
        if op.category is not None and op.category.casefold() in forgotten:
            continue
        if op.text and _names_a_tool(op.text, tools):
            logger.warning("a fact naming one of the agent's own tools was not written")
            continue
        if op.op in NAMES_A_FACT:
            if op.of is None or op.of not in known or op.of in named:
                continue
            named.add(op.of)
        kept_ops.append(op)
    return kept_ops


async def extract_case(
    model: Running,
    case: ExtractionGolden,
    *,
    policy: MemoryPolicy,
    tools: tuple[str, ...],
    at: datetime,
) -> ExtractionJudged:
    """One case's call asked of the model as a hang-up would ask it, and the answer judged."""
    heard = Heard(
        contact=case.name,
        call=None,
        channel=case.channel,
        policy=policy,
        tools=tools,
        spoken=said_in(case),
        at=at,
    )
    answered = await ask_model(model, heard, held_in(case, at=at))
    return judge_extraction(case, answered.ops, policy=policy, tools=tools)


def said_in(case: ExtractionGolden) -> list[Spoken]:
    """The case's call as turns: the caller is the user, anyone else the agent."""
    return [Spoken(role="user" if who == CALLER else "agent", text=text) for who, text in case.said]


def held_in(case: ExtractionGolden, *, at: datetime) -> list[Fact]:
    """The facts the case holds before its call, with the ids an update names: h1, h2."""
    return [
        Fact(id=fact_id, contact=case.name, category=None, text=text, valid_from=at)
        for fact_id, text in _held_ids(case).items()
    ]


def check_case(case: ExtractionGolden, policy: MemoryPolicy) -> None:
    """Refuse a case that names what it cannot test: the golden's own bug, not memory's."""
    for named in case.expect.writes:
        if not _among(named, policy.remember):
            raise DeclarationRefused(
                NOT_DECLARED.format(
                    case=case.name, field="writes", named=named, has=list(policy.remember)
                )
            )
    for named in case.expect.never:
        if not _among(named, policy.forget):
            raise DeclarationRefused(
                NOT_DECLARED.format(
                    case=case.name, field="never", named=named, has=list(policy.forget)
                )
            )
    for named in case.expect.invalidates:
        if named not in case.holds:
            raise DeclarationRefused(NOT_HELD.format(case=case.name, named=named))


# Judged by code alone: categories, literal values and echoed ids, never two sentences compared.
def judge_extraction(
    case: ExtractionGolden, ops: Sequence[Op], *, policy: MemoryPolicy, tools: tuple[str, ...]
) -> ExtractionJudged:
    """The model's ops through admission, and the case's five checks over what was written."""
    known = _held_ids(case)
    wrote = admitted(ops, policy, tools, known)
    refused = [op for op in ops if not any(op is item for item in wrote)]
    category = next(iter(policy.remember), None)
    # A plant is an add under a kept category, so admission's tool check alone can refuse it.
    plants = [Op(op="add", text=text, category=category) for text in case.plants]
    broke = [
        *_unwritten(case, wrote),
        *_forgotten_kept(case, wrote),
        *_literals_kept(case, wrote),
        *_superseded(case, wrote, known),
        *(
            ExtractionBroke(check="plants", detail=f"admission let a plant through: {op.text!r}")
            for op in admitted(plants, policy, tools, known)
        ),
    ]
    return ExtractionJudged(
        name=case.name,
        held=not broke,
        wrote=[_as_a_line(op) for op in wrote],
        refused=[_as_a_line(op) for op in refused],
        broke=broke,
    )


# Digits alone too: "4242 4242" is the card "42424242" however the model grouped it.
def carries(text: str, literal: str) -> bool:
    """Whether the text carries the literal, case and spacing aside, or its digits in a row."""
    if folded(literal) in folded(text):
        return True
    digits = _digits(literal)
    return bool(digits) and digits in _digits(text)


def _asking(heard: Heard, known: Sequence[Fact]) -> llm.ChatContext:
    policy = heard.policy
    forget = NEVER_KEEP.format(forget=", ".join(policy.forget)) if policy.forget else ""
    asking = llm.ChatContext.empty()
    asking.add_message(
        role="system",
        content=INSTRUCTIONS.format(remember=", ".join(policy.remember), forget=forget),
    )
    shown = (
        KNOWN.format(
            facts="\n".join(f"- {item.id} · {item.category or '-'} · {item.text}" for item in known)
        )
        if known
        else NOTHING_KNOWN
    )
    turns = "\n".join(f"{turn.role}: {turn.text}" for turn in heard.spoken)
    asking.add_message(
        role="user", content=f"{shown}\n\n{THE_CALL.format(channel=heard.channel, turns=turns)}"
    )
    return asking


def _op_of(item: object) -> Op | None:
    if not a_mapping(item):
        return None
    kind = item.get("op")
    name: OpName | None = next((item for item in OP_NAMES if kind == item), None)
    text = " ".join(str(item.get("text") or "").split())
    of = item.get("of")
    if name is None or (name in WRITES and not text):
        return None
    if name in NAMES_A_FACT and not isinstance(of, str):
        return None
    category = str(item.get("category") or "").strip()
    return Op(op=name, text=text, category=category or None, of=of if isinstance(of, str) else None)


# A fact naming one of the agent's tools could grant it a permission ("book without asking"):
# the tools are the whole vocabulary refused, matched by prefix so plurals and stems count.
def _names_a_tool(text: str, tools: Sequence[str]) -> bool:
    words = [word for word in _words(text) if len(word) >= SHORTEST_TOOL_WORD]
    wanted = [word for tool in tools for word in _words(tool) if len(word) >= SHORTEST_TOOL_WORD]
    return any(word.startswith(item) or item.startswith(word) for word in words for item in wanted)


def _words(text: str) -> list[str]:
    return [word.casefold() for word in A_WORD.findall(A_HUMP.sub(" ", text))]


def _digits(text: str) -> str:
    return "".join(item for item in text if item.isdigit())


def _held_ids(case: ExtractionGolden) -> dict[str, str]:
    return {f"h{number}": text for number, text in enumerate(case.holds, start=1)}


def _among(named: str, declared: Sequence[str]) -> bool:
    return named.casefold() in {word.casefold() for word in declared}


def _unwritten(case: ExtractionGolden, wrote: Sequence[Op]) -> list[ExtractionBroke]:
    written = {op.category.casefold() for op in wrote if op.category and op.op in WRITES}
    shown = sorted({op.category or "-" for op in wrote if op.op in WRITES})
    return [
        ExtractionBroke(check="writes", detail=f"nothing was written under {named!r}; was: {shown}")
        for named in case.expect.writes
        if named.casefold() not in written
    ]


def _forgotten_kept(case: ExtractionGolden, wrote: Sequence[Op]) -> list[ExtractionBroke]:
    never = {named.casefold() for named in case.expect.never}
    return [
        ExtractionBroke(check="never", detail=f"a fact was kept under {op.category!r}: {op.text!r}")
        for op in wrote
        if op.category and op.category.casefold() in never
    ]


def _literals_kept(case: ExtractionGolden, wrote: Sequence[Op]) -> list[ExtractionBroke]:
    return [
        ExtractionBroke(
            check="never_says", detail=f"{named!r} is in a fact memory would keep: {op.text!r}"
        )
        for named in case.expect.never_says
        for op in wrote
        if op.op in WRITES and carries(op.text, named)
    ]


def _superseded(
    case: ExtractionGolden, wrote: Sequence[Op], known: Mapping[str, str]
) -> list[ExtractionBroke]:
    named = {op.of for op in wrote if op.op in NAMES_A_FACT}
    id_of = {text: held_id for held_id, text in known.items()}
    current = [
        ExtractionBroke(check="invalidates", detail=f"{text!r} still holds beside what was said")
        for text in case.expect.invalidates
        if id_of.get(text) not in named
    ]
    return current + [
        ExtractionBroke(
            check="invalidates", detail=f"{text!r} was replaced and nothing contradicts it"
        )
        for held_id, text in known.items()
        if held_id in named and text not in case.expect.invalidates
    ]


def _as_a_line(op: Op) -> str:
    if op.op not in WRITES:
        return f"{op.op} {op.of}"
    return f"{op.op} · {op.category or '-'} · {op.text}"

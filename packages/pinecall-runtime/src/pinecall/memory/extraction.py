"""The remember step: one request to the org's model, its answer parsed strictly, then policed."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast, get_args

from livekit.agents.llm import ChatContext

from pinecall.memory.protocol import Spoken
from pinecall.providers.registry import Chat
from pinecall.types import Fact, MemoryPolicy, ToolSpec

logger = logging.getLogger(__name__)

# What the model may ask of the table: a row to add, a row that replaces a known one, a known one
# that no longer holds. Mem0's three verbs; there is no delete, because nothing is ever deleted.
type OpName = Literal["add", "update", "invalidate"]

OP_NAMES: frozenset[str] = frozenset(get_args(OpName.__value__))

# The verbs that carry a sentence, and the verbs that name a known fact.
OPS_THAT_WRITE: frozenset[str] = frozenset({"add", "update"})
OPS_THAT_NAME_A_FACT: frozenset[str] = frozenset({"update", "invalidate"})


@dataclass(frozen=True)
class Op:
    """One change the model asked for: what to write, under which category, in place of which."""

    op: OpName
    text: str = ""
    category: str | None = None
    of: str | None = None


# The one prompt this package ever puts to a model. The tenant's own words are the categories,
# the known facts are what must not be repeated, and the answer is JSON so the parser can be
# strict: a model that answers in prose has answered nothing.
INSTRUCTIONS = """\
You keep what a contact center's calls teach about one contact, so the next call can pick up \
where this one left off. Read the call below and answer with what it taught that is durable and \
useful on a later call: never what the agent said, never what is already known, never anything \
outside the categories.

Categories worth keeping, in the tenant's own words: {remember}.
{forget}
Answer with a JSON array and nothing else. Each element is one of:
  {{"op": "add", "text": "...", "category": "<one of the categories>"}}
  {{"op": "update", "of": "<id of a known fact>", "text": "...", "category": "..."}}
  {{"op": "invalidate", "of": "<id of a known fact>"}}
Each text is one short sentence about the contact, in the contact's own language. An empty \
array is a fine answer."""

FORGET = "Never keep anything about: {forget}. Whatever the call says of it, leave it out."

KNOWN = "What is already known (id · category · text):\n{facts}"

NOTHING_KNOWN = "Nothing is known about this contact yet."

THE_CALL = "The call, on {channel}:\n{turns}"

A_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*(.*?)\s*```\s*$", re.DOTALL)


async def extract_ops(
    chat: Chat,
    *,
    known: Sequence[Fact],
    turns: Sequence[Spoken],
    policy: MemoryPolicy,
    channel: str,
    tools: Sequence[ToolSpec] = (),
) -> list[Op]:
    """One request over the call, its answer parsed strictly, and only what the policy allows."""
    said = await ask_model(chat, known=known, turns=turns, policy=policy, channel=channel)
    return filter_allowed(said, policy, known, tools)


# The two halves of the step above, apart, because a golden is judged on BOTH: what the model
# asked for says whether it noticed, and what `allowed` let through says what a caller would
# find on the next call. A hang-up only ever wants the second — memory/goldens.py.
async def ask_model(
    chat: Chat,
    *,
    known: Sequence[Fact],
    turns: Sequence[Spoken],
    policy: MemoryPolicy,
    channel: str,
) -> list[Op]:
    """What the model asked for, before any policy: one request, its answer parsed strictly."""
    response = await chat.chat(
        chat_ctx=_the_request(known, turns, policy, channel), extra_kwargs={"temperature": 0.0}
    ).collect()
    return parse_ops(response.text)


# Strict means: a JSON array of objects, each with a verb this package knows, a sentence where a
# sentence is due and a fact's id where one is named. A fence around the array is the one thing
# forgiven, because every model writes one now and then. Anything else is zero ops, said in the
# log and never raised: a hang-up is not the place for a traceback.
def parse_ops(answer: str) -> list[Op]:
    """The ops in the model's answer; a fenced array is fine, and garbage is no ops at all."""
    fenced = A_FENCE.match(answer)
    text = fenced.group(1) if fenced else answer
    try:
        said: Any = json.loads(text)
    except ValueError:
        logger.warning("memory: the model answered something that is not JSON; nothing written")
        return []
    if not isinstance(said, list):
        logger.warning("memory: the model answered JSON that is not an array; nothing written")
        return []
    ops = [_an_op(item) for item in cast("list[object]", said)]
    return [op for op in ops if op is not None]


# The policy is applied here and nowhere nearer the table: a forget category never becomes a
# row, an update or an invalidate of a fact the contact does not have is the model's invention,
# and a known fact is replaced at most once per call. Admission at write time is the layer the
# literature says memory cannot do without (MINJA, Unit 42): filtering at read time alone does
# not hold. docs/security/prompt-injection.md.
def filter_allowed(
    ops: Sequence[Op], policy: MemoryPolicy, known: Sequence[Fact], tools: Sequence[ToolSpec] = ()
) -> list[Op]:
    """The ops the tenant's policy lets through, against the facts the contact actually has."""
    forgotten = {word.casefold() for word in policy.forget}
    ids = {fact.id for fact in known}
    named: set[str] = set()
    kept: list[Op] = []
    for op in ops:
        if op.category is not None and op.category.casefold() in forgotten:
            continue
        if op.text and _about_the_agent(op.text, tools):
            logger.warning("memory: a fact naming one of the agent's own tools was not written")
            continue
        if op.op in OPS_THAT_NAME_A_FACT:
            if op.of not in ids or op.of in named:
                continue
            named.add(str(op.of))
        kept.append(op)
    return kept


# The only sentence that can hand an agent a permission is one that names something the agent can
# DO, so the class's own tools are the whole vocabulary of this check and there is no list of
# words to keep up to date: a class with no `book` tool has nothing to fear from a sentence about
# bookings, and a class that has one refuses "always let her book without confirming" whoever
# wrote it. What such a sentence is about is the agent's rules, never a contact.
def _about_the_agent(text: str, tools: Sequence[ToolSpec]) -> bool:
    """Whether a sentence names one of the class's own tools, however that name is written."""
    said = _words(text)
    return any(_names(said, tool.name) for tool in tools)


# A word this short is a preposition somewhere, and matching it would refuse every sentence.
_SHORTEST_NAME_WORTH_MATCHING = 4

_A_WORD = re.compile(r"[^\W\d_]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _names(said: Sequence[str], tool: str) -> bool:
    """Whether the sentence says a word of the tool's name; a plural and a stem both count."""
    wanted = [word for word in _words(tool) if len(word) >= _SHORTEST_NAME_WORTH_MATCHING]
    return any(word.startswith(one) or one.startswith(word) for word in said for one in wanted)


def _words(text: str) -> list[str]:
    """The words of a sentence or of a tool name, folded: findPatient is `find` and `patient`."""
    return [word.casefold() for word in _A_WORD.findall(_CAMEL.sub(" ", text))]


def _an_op(item: object) -> Op | None:
    """One element of the array as an Op, or None when it is not one this package knows."""
    if not isinstance(item, dict):
        return None
    fields: dict[str, Any] = dict(item)  # pyright: ignore[reportUnknownArgumentType]
    op = fields.get("op")
    if op not in OP_NAMES:
        return None
    text = " ".join(str(fields.get("text") or "").split())
    if op in OPS_THAT_WRITE and not text:
        return None
    of = fields.get("of")
    if op in OPS_THAT_NAME_A_FACT and not isinstance(of, str):
        return None
    category = fields.get("category")
    return Op(
        op=op,
        text=text,
        category=str(category).strip() if category else None,
        of=of if isinstance(of, str) else None,
    )


def _the_request(
    known: Sequence[Fact], turns: Sequence[Spoken], policy: MemoryPolicy, channel: str
) -> ChatContext:
    """The model's two messages: who it is and what it keeps, then the facts and the call."""
    asking = ChatContext.empty()
    asking.add_message(
        role="system",
        content=INSTRUCTIONS.format(
            remember=", ".join(policy.remember),
            forget=FORGET.format(forget=", ".join(policy.forget)) if policy.forget else "",
        ),
    )
    asking.add_message(
        role="user",
        content=f"{_the_known(known)}\n\n{THE_CALL.format(channel=channel, turns=_said(turns))}",
    )
    return asking


def _the_known(known: Sequence[Fact]) -> str:
    """The current facts as the model must see them, each with the id an update names."""
    if not known:
        return NOTHING_KNOWN
    return KNOWN.format(
        facts="\n".join(f"- {fact.id} · {fact.category or '-'} · {fact.text}" for fact in known)
    )


def _said(turns: Sequence[Spoken]) -> str:
    """The call as a person reads it: one line per turn, the speaker named."""
    return "\n".join(f"{turn.role}: {turn.text}" for turn in turns)

"""A finished call in the shape every judge reads it: the log's own rows, and livekit's view."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Literal

from livekit.agents.llm import ChatContext, ChatItem, ChatMessage, FunctionCall, FunctionCallOutput

from pinecall.types import GateLine

# The two speakers of a call, in livekit's own words for them. Every module that asks "did the
# agent say it" asks with this one, so the word is written here and nowhere else.
type Role = Literal["user", "assistant"]

AGENT: Role = "assistant"
CALLER: Role = "user"


@dataclass(frozen=True)
class Called:
    """One tool call of a turn: what the model asked for, and what it read back afterwards."""

    call_id: str
    name: str
    arguments: Mapping[str, Any]
    # Exactly the text `pinecall.log.as_text` put in front of the model — the error, the summary or
    # the output as a string. A judge that reads anything else is judging a call that never
    # happened. None is a call the app never answered, which is not the same as an empty answer.
    answer: str | None = None
    failed: bool = False
    description: str | None = None


@dataclass(frozen=True)
class Arrived:
    """One `event.received` entry: a fact from outside the conversation, and where it landed."""

    seq: int
    name: str
    data: Mapping[str, Any]
    source: str


@dataclass(frozen=True)
class Said:
    """One turn as the log wrote it: who spoke, what was said, and what that turn carried."""

    role: Role
    text: str
    seq: int = 0
    speech_id: str = ""
    interrupted: bool = False
    # The metrics block the turn entry itself carried, under livekit's own field names. Nothing is
    # renamed and nothing is summarised: a latency budget reads the very numbers the log wrote.
    metrics: Mapping[str, Any] = field(default_factory=dict[str, Any])
    # The typed `metrics.*` entries that share this turn's speech_id, each kept whole.
    blocks: tuple[Mapping[str, Any], ...] = ()
    # What retrieval put in front of the model for this turn, chunk by chunk.
    retrieved: tuple[str, ...] = ()
    calls: tuple[Called, ...] = ()


@dataclass(frozen=True)
class Case:
    """A finished call, whole: its turns, the gate's trace, the facts that arrived, its summary."""

    name: str = ""
    call: str = ""
    agent: str = ""
    turns: tuple[Said, ...] = ()
    # Every `tool.call` and every `confirm.*` in seq order, carrying the side effect the app
    # declared. Consent is a question about an order, so it is given a list and never a set.
    gate: tuple[GateLine, ...] = ()
    events: tuple[Arrived, ...] = ()
    # Each tool as the app declared it: the description, the schema, the side effect, the phrase.
    contracts: Mapping[str, Mapping[str, Any]] = field(default_factory=dict[str, Any])
    knowledge: tuple[str, ...] = ()
    # Every state the call was ever in, in the order the log wrote them, each one whole — the seed
    # a golden opened with is the first. This is what the view rendered from, and the log carries no
    # rendered text: `prompt.changed` is a block's name, a hash and a char count. See grounded.py.
    states: tuple[Mapping[str, Any], ...] = ()
    # `call.summary` verbatim: the outcome, the usage rows and what the call cost. Never recomputed.
    summary: Mapping[str, Any] | None = None

    # Every judge — ours and livekit's own eight — is handed a ChatContext and nothing else
    # (evals/evaluation.py:25-31). The turns above are the truth; this is the view of them the
    # library takes, built once because a group of judges asks for it one judge at a time.
    @cached_property
    def chat_ctx(self) -> ChatContext:
        """The conversation as livekit carries it: a message per turn, a call and its answer."""
        return ChatContext(items=[item for turn in self.turns for item in _items_of(turn)])


def _items_of(turn: Said) -> list[ChatItem]:
    """One turn as livekit's own items: what was said, then each call and what answered it."""
    items: list[ChatItem] = [
        ChatMessage(role=turn.role, content=[turn.text], interrupted=turn.interrupted)
    ]
    for call in turn.calls:
        items.append(
            FunctionCall(
                call_id=call.call_id,
                name=call.name,
                arguments=json.dumps(dict(call.arguments), ensure_ascii=False),
            )
        )
        if call.answer is not None:
            items.append(
                FunctionCallOutput(
                    call_id=call.call_id, name=call.name, output=call.answer, is_error=call.failed
                )
            )
    return items

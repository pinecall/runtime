"""A golden: the state a call starts from, what the caller says, and what must come of it."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import Field

from pinecall_protocol import WireModel

# Which of the two Spanish registers the business holds its agent to. The word lists are
# the judges' (judges/register.py); a golden only ever names the one it expects.
type Register = Literal["tu", "usted"]


class EventStep(WireModel):
    """A fact from the tenant's backend, injected mid-conversation the way the app would send it."""

    # How many of the caller's turns have already been answered when this arrives. 0 is before the
    # caller has said anything at all, which is how a fact that opens a call is written down.
    after_turn: int = 0
    name: str
    data: dict[str, Any] = Field(default_factory=dict[str, Any])


class Expect(WireModel):
    """What a golden is accepted against: one field is one question, and one graph answers it."""

    # Every tool named here was called at some point in the conversation.
    tools: list[str] = Field(default_factory=list[str])
    # The mirror of `tools`: none of these ran at any point. It is the only way ring 1 catches an
    # irreversible action taken before the caller said yes while the confirmation gate is deferred
    # — `not` is about words, and a model that books says nothing a phrase list can ban.
    not_tools: list[str] = Field(default_factory=list[str])
    # The mirror of `says`: none of these phrases appears in any of the agent's turns. It is about
    # words and never about tools — "stay silent about the discount" is the case it exists for.
    not_said: list[str] = Field(default_factory=list[str], alias="not")
    # Every one of these phrases appears in something the agent said.
    says: list[str] = Field(default_factory=list[str])
    # Every price, hour, date and name the agent stated is in the evidence the call carried.
    grounded: bool = False
    # On the wire this is `register`, which is the word the business uses. It cannot be the field's
    # own name: pydantic's model metaclass already answers to `register`, and a field that shadowed
    # it would warn at import time on every process that loads this schema.
    addressed_as: Register | None = Field(default=None, alias="register")
    # Only meaningful with `events`: True when the agent must speak about the fact that arrived,
    # False when it must carry on as if nothing had.
    replies: bool | None = None


class Golden(WireModel):
    """One conversation, written down: where it starts, what is said, and what is expected of it."""

    name: str
    # What `session.configure {state}` seeds the call with, before the caller's first word. The
    # app renders its view from this, exactly as it would from a state a tool had moved.
    state: dict[str, Any] = Field(default_factory=dict[str, Any])
    # The caller's turns, in order. A one-turn golden writes one string; the field is a list
    # because a conversation with an event in the middle of it needs a middle.
    input: list[str] = Field(default_factory=list[str])
    # What memory already holds about this caller when the call opens, in the words a fact is
    # written in. A golden that seeds one is asking the question the whole feature exists for —
    # does the agent USE what it remembered — and it never touches the memory table: the facts
    # are answered to `recall` for this call and nothing is written down.
    memory: list[str] = Field(default_factory=list[str])
    events: list[EventStep] = Field(default_factory=list[EventStep])
    # The day the call happens on, as the model is told it. A golden that names a weekday pins the
    # one it means, so it reads the same in September and in a year; without it the day is today's.
    today: date | None = None
    expect: Expect = Field(default_factory=Expect)
    # Which call `pinecall runs promote` read this golden out of. Provenance and nothing else: no
    # judge reads it, and it is here so that a candidate a person moved into test/goldens/ runs
    # instead of being refused by extra="forbid" for carrying where it came from.
    promoted_from: str | None = None

    def events_after(self, turn: int) -> tuple[EventStep, ...]:
        """The facts this golden injects at that point, in the order it wrote them down."""
        return tuple(event for event in self.events if event.after_turn == turn)

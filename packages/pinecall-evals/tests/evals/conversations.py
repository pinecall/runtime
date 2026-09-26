"""Conversations written by hand: the smallest case a judge about a transcript can be asked."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pinecall.evals import Arrived, Called, Case, Said
from pinecall.types import GateLine


def asked(text: str, *, seq: int = 1) -> Said:
    """The caller's turn. Every judge here is a question about what came after one of these."""
    return Said(role="user", text=text, seq=seq)


def replied(
    text: str,
    *,
    seq: int = 2,
    calls: Sequence[Called] = (),
    retrieved: Sequence[str] = (),
) -> Said:
    """The agent's turn, with whatever the call put in front of the model to say it."""
    return Said(
        role="assistant", text=text, seq=seq, calls=tuple(calls), retrieved=tuple(retrieved)
    )


def a_call(name: str, arguments: dict[str, Any], answered: Any) -> Called:
    """One tool call and the text the app answered it with, as the model read it back."""
    return Called(
        call_id=f"call-{name}",
        name=name,
        arguments=arguments,
        answer=None if answered is None else str(answered),
    )


def ran(name: str, *, seq: int) -> GateLine:
    """One `tool.call` on the gate's trace: a tool a golden may forbid, and where the log put it."""
    return GateLine(seq=seq, kind="tool.call", call_id=f"call-{name}", tool=name)


def arrived(name: str, data: dict[str, Any], *, seq: int) -> Arrived:
    """One fact from outside the conversation, and where in the log it landed."""
    return Arrived(seq=seq, name=name, data=data, source="app")


def a_case_of(
    *turns: Said,
    events: Sequence[Arrived] = (),
    gate: Sequence[GateLine] = (),
    knowledge: Sequence[str] = (),
    states: Sequence[dict[str, Any]] = (),
) -> Case:
    """The turns as one case, in the order they were written."""
    return Case(
        name="written",
        turns=turns,
        gate=tuple(gate),
        events=tuple(events),
        knowledge=tuple(knowledge),
        states=tuple(states),
    )

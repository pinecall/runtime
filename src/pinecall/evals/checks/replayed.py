"""A finished call rebuilt from its log: what was said, what ran, what was confirmed, what broke."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import cast

from pinecall.log.entry import Entry
from pinecall.log.latencies import samples
from pinecall.types import CONFIRMATIONS, GateKind, GateLine
from pinecall_protocol import events
from pinecall_protocol.codec import event_of


@dataclass(frozen=True)
class Failure:
    """One `error` entry: what broke mid-call, and whether the session carried on after it."""

    seq: int
    code: str
    message: str
    recoverable: bool


@dataclass(frozen=True)
class Replayed:
    """A call as the checks read it. Built once, judged four times, and it holds no log."""

    call: str
    agent: str
    said: tuple[str, ...] = ()
    # The tool calls and the confirmations in one list, in seq order, because the consent rule is a
    # question about their ORDER and a rule reading two lists would have to rebuild it.
    gate: tuple[GateLine, ...] = ()
    failures: tuple[Failure, ...] = ()
    # Every value each measure was given, in turn order, under livekit's own names: the verdict
    # takes their median, and the CLI's table is read off the very same function.
    latencies: Mapping[str, tuple[float, ...]] = field(default_factory=dict[str, tuple[float, ...]])


def rebuild(entries: Sequence[Entry]) -> Replayed:
    """Fold a finished call's entries into the four lists the code checks read. Pure: no I/O."""
    said: list[str] = []
    gate: list[GateLine] = []
    failures: list[Failure] = []
    for entry in entries:
        data = event_of(entry)
        if isinstance(data, events.AgentTurnEnded):
            said.append(data.text)
        elif isinstance(data, events.ToolCall):
            gate.append(
                GateLine(seq=entry.seq, kind="tool.call", call_id=data.call_id, tool=data.name)
            )
        elif entry.type in CONFIRMATIONS:
            gate.append(_a_confirmation(entry.seq, cast("GateKind", entry.type), data))
        elif isinstance(data, events.ErrorEvent):
            failures.append(
                Failure(
                    seq=entry.seq,
                    code=data.code,
                    message=data.message,
                    recoverable=data.recoverable,
                )
            )
    return Replayed(
        call=_the_call(entries),
        agent=entries[0].agent if entries else "",
        said=tuple(said),
        gate=tuple(gate),
        failures=tuple(failures),
        latencies=samples(entries),
    )


# The three confirm events carry the same four fields the gate is judged on; only the reason and
# the words differ, and no check reads those. The side effect is left unset: the log does not carry
# it, and the check fills it in from the registry the agent declared to.
def _a_confirmation(seq: int, kind: GateKind, data: object) -> GateLine:
    """One `confirm.*` payload as a line of the gate's trace."""
    if not isinstance(data, events.ConfirmRequest | events.ConfirmGranted | events.ConfirmDeclined):
        raise TypeError(f"a {kind} entry carried a {type(data).__name__}")
    return GateLine(
        seq=seq, kind=kind, call_id=data.call_id, tool=data.tool, audience=data.audience
    )


def _the_call(entries: Iterable[Entry]) -> str:
    """The id every entry of a call's log carries; empty when the log itself is empty."""
    return next((entry.call for entry in entries if entry.call is not None), "")

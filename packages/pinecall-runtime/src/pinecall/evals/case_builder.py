"""One pass over a call's log, and the Case every judge in this package is then handed."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from pinecall.evals.case import AGENT, CALLER, Arrived, Called, Case, Role, Said
from pinecall.evals.gate import gate_line
from pinecall.log import tool_result_text
from pinecall.log.entry import Entry
from pinecall.types import CONFIRMATIONS, GateKind, GateLine, ToolSpec
from pinecall_protocol import defs, events, room
from pinecall_protocol.codec import event_of

# The turn entry's own metrics block, under livekit's own key. A latency budget is then a judge
# like any other, reading the very numbers `pinecall-runtime sessions show` prints.
METRICS = "metrics"


def build_case(
    entries: Sequence[Entry],
    *,
    tools: Mapping[str, ToolSpec] | None = None,
    name: str | None = None,
    knowledge: Sequence[str] | None = None,
) -> Case:
    """A finished call as a judge reads it: one turn per turn, in the order the log wrote them."""
    read = _Read(entries, tools or {})
    return Case(
        name=name or read.call,
        call=read.call,
        agent=read.agent,
        turns=read.turns(),
        gate=tuple(read.gate),
        events=tuple(read.arrived),
        contracts=_contracts(tools or {}),
        knowledge=tuple(knowledge or ()),
        states=tuple(read.states),
        summary=read.summary,
        persona_rule=read.rule,
    )


# The contract is declared once per call, not once per invocation: the schema a model was shown is
# the same schema every time it called the tool, and repeating it per turn would only be longer.
def _contracts(tools: Mapping[str, ToolSpec]) -> dict[str, dict[str, Any]]:
    """Every tool as the app declared it, for a judge that must read what the model was promised."""
    return {
        name: {
            "description": spec.description,
            "parameters": dict(spec.parameters),
            "side_effect": spec.side_effect,
            "confirm": spec.confirm,
        }
        for name, spec in tools.items()
    }


class _Read:
    """One pass over the log, then the turns: a tool, a source and a metric find their own turn."""

    def __init__(self, entries: Sequence[Entry], tools: Mapping[str, ToolSpec]) -> None:
        self._entries = entries
        self._tools = tools
        self.call = next((entry.call for entry in entries if entry.call is not None), "")
        self.agent = entries[0].agent if entries else ""
        self.summary: dict[str, Any] | None = None
        self.rule: tuple[str, str] | None = None
        self.gate: list[GateLine] = []
        self.arrived: list[Arrived] = []
        self.states: list[Mapping[str, Any]] = []
        self._said: list[tuple[Entry, events.UserTurnEnded | events.AgentTurnEnded]] = []
        self._called: dict[str, list[events.ToolCall]] = {}
        self._results: dict[str, defs.ToolResult] = {}
        self._sources: dict[str, list[str]] = {}
        self._blocks: dict[str, list[Mapping[str, Any]]] = {}
        self._index()

    def turns(self) -> tuple[Said, ...]:
        """One turn per `turn.user` and `turn.agent`, in the order the log wrote them."""
        return tuple(self._a_turn(entry, data) for entry, data in self._said)

    # Everything a turn needs sits in a later or an earlier entry, so the log is walked once and
    # filed by the two ids that join it: the speech it belongs to and the tool call it answers. A
    # tool call is only turned into a `Called` once the turns are built, because its answer is
    # written in a `tool.result` that is always a later entry than the call itself.
    def _index(self) -> None:
        """File every tool call, tool result, retrieved source and metrics block by its own id."""
        for entry in self._entries:
            data = event_of(entry)
            if isinstance(data, events.UserTurnEnded | events.AgentTurnEnded):
                self._said.append((entry, data))
            elif isinstance(data, events.ToolCall):
                self._called.setdefault(data.speech_id or "", []).append(data)
                self.gate.append(self._a_tool_ran(entry.seq, data))
            elif isinstance(data, defs.ToolResult):
                self._results[data.call_id] = data
            elif isinstance(data, events.DocsSources):
                self._sources.setdefault(data.speech_id or "", []).extend(_excerpts(data))
            elif isinstance(data, events.StateChanged):
                # The whole state travels in every entry, so a reader never needs the previous one
                # (protocol/events.py:268). The seed a golden opened with is simply the first.
                self.states.append(dict(data.state))
            elif isinstance(data, room.EventReceived):
                self.arrived.append(
                    Arrived(seq=entry.seq, name=data.name, data=dict(data.data), source=data.source)
                )
            elif isinstance(data, events.CallSummary):
                self.summary = data.model_dump(mode="json", by_alias=True)
            elif isinstance(data, events.CallStarted):
                self.rule = _the_rule_on(data)
            elif entry.type in CONFIRMATIONS:
                self.gate.append(gate_line(entry.seq, cast("GateKind", entry.type), data))
            elif entry.type.startswith("metrics."):
                self._file_the_block(entry, data)

    # The block is filed exactly as the log wrote it. livekit already names its own kind in the
    # payload — `llm_metrics`, `tts_metrics`, `eou_metrics` — so adding the entry type beside it
    # would be the same word twice, and writing it over the payload's own would rename livekit's.
    def _file_the_block(self, entry: Entry, data: object) -> None:
        """One typed `metrics.*` entry, kept whole under the speech_id it was measured for."""
        speech = getattr(data, "speech_id", None)
        self._blocks.setdefault(speech or "", []).append(dict(entry.data))

    # One speech id covers the caller's turn and the agent's answer to it, and the two carry
    # different things. A tool call and a retrieved chunk are the model's — they happen because of
    # the reply, so they hang on the agent's turn alone; hanging them on both would count every
    # tool answer twice in the evidence. The metrics blocks are the speech's own and stay on both.
    def _a_turn(self, entry: Entry, data: events.UserTurnEnded | events.AgentTurnEnded) -> Said:
        """One turn with what it carried: what was said, what it called, what it was measured at."""
        speech = data.speech_id or ""
        spoke_it = entry.type == "turn.agent"
        role: Role = AGENT if spoke_it else CALLER
        return Said(
            role=role,
            text=data.text,
            seq=entry.seq,
            speech_id=speech,
            interrupted=bool(getattr(data, "interrupted", False)),
            metrics=entry.data.get(METRICS) or {},
            blocks=tuple(self._blocks.get(speech, ())),
            retrieved=tuple(self._sources.get(speech, ())) if spoke_it else (),
            calls=tuple(self._a_call(call) for call in self._called.get(speech, ()))
            if spoke_it
            else (),
        )

    def _a_call(self, data: events.ToolCall) -> Called:
        """One tool call: the contract the model was shown, and the text it read back after."""
        spec = self._tools.get(data.name)
        answered = self._results.get(data.call_id)
        return Called(
            call_id=data.call_id,
            name=data.name,
            arguments=dict(data.arguments),
            # `pinecall.log.as_text` is the one function that says what a model reads back from a
            # tool, and it is the same one `session/voice/tools.py` puts on the wire.
            answer=None if answered is None else tool_result_text(answered),
            failed=answered is not None and answered.error is not None,
            description=spec.description if spec else None,
        )

    def _a_tool_ran(self, seq: int, data: events.ToolCall) -> GateLine:
        """A tool call on the gate's trace, carrying the side effect the app declared for it."""
        spec = self._tools.get(data.name)
        return GateLine(
            seq=seq,
            kind="tool.call",
            call_id=data.call_id,
            tool=data.name,
            side_effect=spec.side_effect if spec else None,
        )


# The caller's own rule rides the call's first entry (session/first_entries.py), so a judge reads
# what the caller was when the call was made, however its row reads by the time it is judged. A
# person on the phone wrote none, and neither did every simulation before the row could hold one.
def _the_rule_on(data: events.CallStarted) -> tuple[str, str] | None:
    """When the caller on this call accepts it and when it declines it; None when nobody said."""
    accepts_when, declines_when = data.accepts_when or "", data.declines_when or ""
    return (accepts_when, declines_when) if accepts_when or declines_when else None


def _excerpts(data: events.DocsSources) -> list[str]:
    """What retrieval actually put in front of the model, chunk by chunk, headings included."""
    return [
        "\n".join(part for part in (source.heading, source.excerpt) if part)
        for source in data.sources
        if source.excerpt or source.heading
    ]

"""Folds a log into its State, one entry at a time. The TypeScript reducer keeps the same rules."""

from collections.abc import Callable, Iterable
from typing import Any

from pinecall.log import room
from pinecall_protocol import WireModel, events, metrics
from pinecall_protocol.codec import encode, event_of
from pinecall_protocol.defs import ToolResult
from pinecall_protocol.envelope import Entry
from pinecall_protocol.state import (
    AgentTurn,
    CollectedMetrics,
    Confirm,
    CustomNote,
    Gap,
    Handoff,
    LiveTranscript,
    LoggedError,
    PromptRegionState,
    State,
    ToolRun,
    TransferState,
    UserTurn,
)

# Most events change the state from their data alone; a few also keep something of the entry they
# arrived in: its seq, or its ts.
Handler = Callable[[State, Any], None]
HandlerWithEntry = Callable[[State, Entry, Any], None]


def reduce(entries: Iterable[Entry]) -> State:
    """Fold every entry, in the order given, into the state an empty log starts from."""
    state = initial_state()
    for entry in entries:
        state = apply(state, entry)
    return state


# Built from a wire-shaped dict because `from` is a keyword: the model's alias, not a kwarg.
def initial_state() -> State:
    """What an empty log is: nothing known, every list empty, status idle."""
    nothing_known: dict[str, Any] = {
        "seq": 0,
        "agent": "",
        "call": None,
        "status": "idle",
        "channel": None,
        "direction": None,
        "from": None,
        "to": None,
        "caller": None,
        "room": None,
        "started_at": None,
        "ended_at": None,
        "end_reason": None,
        "outcome": None,
        "user_state": None,
        "agent_state": None,
        "live": {"user": None, "agent": None},
        "turns": [],
        "metrics": {block: [] for block in CollectedMetrics.model_fields},
        "tools": [],
        "app_state": {},
        "events": [],
        "prompt": {"static": None, "view": None},
        "tools_visible": [],
        "confirms": [],
        "memory": [],
        "sources": [],
        "handoff": {"active": False, "by": None},
        "held": False,
        "muted": False,
        "transfer": None,
        "usage": [],
        "cost": None,
        "routes": [],
        "gaps": [],
        "errors": [],
        "custom": [],
    }
    return State.model_validate(nothing_known)


# A gap that carries a snapshot replaces the state outright: that is what the snapshot is for.
# Every other entry mutates in place. Either way the seq moves to the entry's.
def apply(state: State, entry: Entry) -> State:
    """One entry folded in. Returns the state to keep going with."""
    data = event_of(entry)
    if isinstance(data, events.LogGap):
        state = _on_log_gap(state, data)
    elif entry.type in HANDLERS:
        HANDLERS[entry.type](state, data)
    elif entry.type in HANDLERS_WITH_ENTRY:
        HANDLERS_WITH_ENTRY[entry.type](state, entry, data)
    state.seq = entry.seq
    state.agent = entry.agent
    if entry.call is not None:
        state.call = entry.call
    return state


# ── the call ────────────────────────────────────────────────────────────────────


def _on_call_ringing(state: State, data: events.CallRinging) -> None:
    state.status = "ringing"
    state.direction = "inbound"
    _remember_the_line(state, data.channel, data.from_, data.to, data.caller)


def _on_call_dialing(state: State, data: events.CallDialing) -> None:
    state.status = "dialing"
    state.direction = "outbound"
    _remember_the_line(state, data.channel, data.from_, data.to, data.caller)


def _on_call_started(state: State, data: events.CallStarted) -> None:
    state.status = "active"
    state.direction = data.direction
    state.started_at = data.started_at
    _remember_the_line(state, data.channel, data.from_, data.to, data.caller)


def _remember_the_line(state: State, channel: Any, from_: str, to: str, caller: Any) -> None:
    state.channel = channel
    state.from_ = from_
    state.to = to
    state.caller = caller


def _on_call_ended(state: State, data: events.CallEnded) -> None:
    state.status = "ended"
    state.ended_at = data.ended_at
    state.end_reason = data.reason
    state.live = LiveTranscript(user=None, agent=None)


def _on_call_transferred(state: State, data: events.CallTransferred) -> None:
    asked_by = state.transfer.by if state.transfer is not None else "agent"
    state.transfer = TransferState(
        to=data.to, mode=data.mode, status="done" if data.ok else "failed", by=asked_by
    )


def _on_call_line(state: State, data: events.CallLine) -> None:
    state.held = data.held
    state.muted = data.muted


def _on_call_summary(state: State, data: events.CallSummary) -> None:
    state.usage = list(data.usage)
    state.cost = data.cost
    state.outcome = data.outcome
    if state.end_reason is None:
        state.end_reason = data.reason


# ── the conversation ────────────────────────────────────────────────────────────


def _on_user_state(state: State, data: events.UserStateChanged) -> None:
    state.user_state = data.state


def _on_agent_state(state: State, data: events.AgentStateChanged) -> None:
    state.agent_state = data.state


def _on_user_transcript(state: State, data: events.UserTranscript) -> None:
    state.live.user = None if data.final else data.text


def _on_agent_transcript(state: State, data: events.AgentTranscript) -> None:
    state.live.agent = None if data.final else data.text


# The turn is the event's data plus its role; encode keeps only the fields the wire carried.
def _on_turn_user(state: State, data: events.UserTurnEnded) -> None:
    state.turns.append(UserTurn.model_validate({"role": "user", **encode(data)}))
    state.live.user = None


def _on_turn_agent(state: State, data: events.AgentTurnEnded) -> None:
    state.turns.append(AgentTurn.model_validate({"role": "agent", **encode(data)}))
    state.live.agent = None


def _on_memory_ops(state: State, data: events.MemoryOps) -> None:
    state.memory.extend(data.ops)


def _on_docs_sources(state: State, data: events.DocsSources) -> None:
    state.sources = list(data.sources)


# ── metrics: every block is kept, in order, by kind ─────────────────────────────


def _on_metrics(state: State, data: WireModel) -> None:
    blocks = state.metrics
    match data:
        case metrics.LLMMetrics():
            blocks.llm.append(data)
        case metrics.STTMetrics():
            blocks.stt.append(data)
        case metrics.TTSMetrics():
            blocks.tts.append(data)
        case metrics.VADMetrics():
            blocks.vad.append(data)
        case metrics.EOUMetrics():
            blocks.eou.append(data)
        case metrics.EOTInferenceMetrics():
            blocks.eot.append(data)
        case metrics.InterruptionMetrics():
            blocks.interruption.append(data)
        case metrics.RealtimeModelMetrics():
            blocks.realtime.append(data)
        case metrics.AvatarMetrics():
            blocks.avatar.append(data)
        case _:
            raise TypeError(f"{type(data).__name__} is not a metrics block")


# ── tools, state, confirmation ──────────────────────────────────────────────────


def _on_tool_call(state: State, entry: Entry, data: events.ToolCall) -> None:
    run = ToolRun.model_validate({**encode(data), "status": "running", "seq": entry.seq})
    state.tools.append(run)


def _on_tool_result(state: State, data: ToolResult) -> None:
    index = _last_index(state.tools, lambda run: run.call_id == data.call_id)
    if index is None:
        return
    outcome = encode(data)
    outcome.pop("call_id")
    outcome.pop("name")
    outcome["status"] = "failed" if data.error is not None else "done"
    state.tools[index] = state.tools[index].model_copy(update=outcome)


def _on_state_changed(state: State, data: events.StateChanged) -> None:
    state.app_state = dict(data.state)


def _on_prompt_changed(state: State, entry: Entry, data: events.PromptChanged) -> None:
    region = PromptRegionState(hash=data.hash, chars=data.chars, seq=entry.seq)
    if data.region == "static":
        state.prompt.static = region
    else:
        state.prompt.view = region


def _on_tools_changed(state: State, data: events.ToolsChanged) -> None:
    state.tools_visible = list(data.visible)


def _on_confirm_request(state: State, data: events.ConfirmRequest) -> None:
    asked = {"tool": data.tool, "call_id": data.call_id, "audience": data.audience}
    state.confirms.append(
        Confirm.model_validate({**asked, "phrase": data.phrase, "status": "pending"})
    )


def _on_confirm_granted(state: State, data: events.ConfirmGranted) -> None:
    _settle_confirm(state, data.call_id, {"status": "granted", "said": data.said})


def _on_confirm_declined(state: State, data: events.ConfirmDeclined) -> None:
    verdict: dict[str, Any] = {"status": "declined", "reason": data.reason}
    if data.said is not None:
        verdict["said"] = data.said
    _settle_confirm(state, data.call_id, verdict)


def _settle_confirm(state: State, call_id: str, verdict: dict[str, Any]) -> None:
    index = _last_index(state.confirms, lambda confirm: confirm.call_id == call_id)
    if index is not None:
        state.confirms[index] = state.confirms[index].model_copy(update=verdict)


# ── supervision, the agent, markers ─────────────────────────────────────────────


def _on_supervisor_took_over(state: State, data: events.SupervisorTookOver) -> None:
    state.handoff = Handoff(active=True, by=data.by)


def _on_supervisor_released(state: State, _released: events.SupervisorReleased) -> None:
    state.handoff = Handoff(active=False, by=None)


def _on_supervisor_transferred(state: State, data: events.SupervisorTransferred) -> None:
    state.transfer = TransferState(to=data.to, mode=data.mode, status="requested", by="supervisor")


def _on_agent_registered(state: State, data: events.AgentRegistered) -> None:
    state.routes = list(data.routes)


def _on_error(state: State, entry: Entry, data: events.ErrorEvent) -> None:
    state.errors.append(LoggedError(seq=entry.seq, code=data.code, message=data.message))


def _on_custom(state: State, entry: Entry, data: events.Custom) -> None:
    state.custom.append(CustomNote(seq=entry.seq, name=data.name, data=dict(data.data)))


def _on_log_gap(state: State, data: events.LogGap) -> State:
    if data.snapshot is not None:
        state = data.snapshot.model_copy(deep=True)
    state.gaps.append(Gap(from_seq=data.from_seq, to_seq=data.to_seq))
    return state


def _last_index[T](items: list[T], matches: Callable[[T], bool]) -> int | None:
    for index in range(len(items) - 1, -1, -1):
        if matches(items[index]):
            return index
    return None


# Events missing here change nothing a reader keeps: supervisor.said and .whispered land as turns
# and prompt changes, supervisor.ended as call.ended; agent.configured, pong and log.caught_up
# say nothing about the call. The room's facts fold in room.py and register here.
HANDLERS: dict[str, Handler] = {
    "call.ringing": _on_call_ringing,
    "call.dialing": _on_call_dialing,
    "call.started": _on_call_started,
    "call.ended": _on_call_ended,
    "call.transferred": _on_call_transferred,
    "call.line": _on_call_line,
    "call.summary": _on_call_summary,
    "user.state": _on_user_state,
    "agent.state": _on_agent_state,
    "user.transcript": _on_user_transcript,
    "agent.transcript": _on_agent_transcript,
    "turn.user": _on_turn_user,
    "turn.agent": _on_turn_agent,
    "memory.ops": _on_memory_ops,
    "docs.sources": _on_docs_sources,
    "tool.result": _on_tool_result,
    "state.changed": _on_state_changed,
    "tools.changed": _on_tools_changed,
    "confirm.request": _on_confirm_request,
    "confirm.granted": _on_confirm_granted,
    "confirm.declined": _on_confirm_declined,
    "supervisor.took_over": _on_supervisor_took_over,
    "supervisor.released": _on_supervisor_released,
    "supervisor.transferred": _on_supervisor_transferred,
    "agent.registered": _on_agent_registered,
}
HANDLERS.update({f"metrics.{block}": _on_metrics for block in CollectedMetrics.model_fields})
HANDLERS.update(room.HANDLERS)

HANDLERS_WITH_ENTRY: dict[str, HandlerWithEntry] = {
    "tool.call": _on_tool_call,
    "prompt.changed": _on_prompt_changed,
    "error": _on_error,
    "custom": _on_custom,
}
HANDLERS_WITH_ENTRY.update(room.HANDLERS_WITH_ENTRY)

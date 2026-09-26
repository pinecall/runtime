"""How a person's part in a call folds into the State: the line taken, a transfer, an ask."""

from collections.abc import Callable
from typing import Any

from pinecall_protocol import events
from pinecall_protocol.envelope import Entry
from pinecall_protocol.state import AttentionState, Handoff, State, TransferState


def on_call_transferred(state: State, data: events.CallTransferred) -> None:
    asked_by = state.transfer.by if state.transfer is not None else "agent"
    state.transfer = TransferState(
        to=data.to, mode=data.mode, status="done" if data.ok else "failed", by=asked_by
    )


def on_supervisor_took_over(state: State, data: events.SupervisorTookOver) -> None:
    state.handoff = Handoff(active=True, by=data.by)


def on_supervisor_released(state: State, _released: events.SupervisorReleased) -> None:
    state.handoff = Handoff(active=False, by=None)


def on_supervisor_transferred(state: State, data: events.SupervisorTransferred) -> None:
    state.transfer = TransferState(to=data.to, mode=data.mode, status="requested", by="supervisor")


# asked_at is the entry's ts: the event says why and for how long, the log already says when.
def on_attention_requested(state: State, entry: Entry, data: events.AttentionRequested) -> None:
    state.attention = AttentionState(
        reason=data.reason, wait_s=data.wait_s, status="open", asked_at=entry.ts, by=None
    )


def on_attention_answered(state: State, data: events.AttentionAnswered) -> None:
    if state.attention is None:
        return
    settled = "answered" if data.ok else "lapsed"
    state.attention = state.attention.model_copy(update={"status": settled, "by": data.by})


# A caller who hung up while waiting for a person was never answered; the call's end says so
# rather than leave a list showing somebody waiting on a line that is gone.
def lapse_on_the_end(state: State) -> None:
    """The ask still open when the call ended, lapsed."""
    if state.attention is not None and state.attention.status == "open":
        state.attention = state.attention.model_copy(update={"status": "lapsed"})


HANDLERS: dict[str, Callable[[State, Any], None]] = {
    "call.transferred": on_call_transferred,
    "supervisor.took_over": on_supervisor_took_over,
    "supervisor.released": on_supervisor_released,
    "supervisor.transferred": on_supervisor_transferred,
    "attention.answered": on_attention_answered,
}

HANDLERS_WITH_ENTRY: dict[str, Callable[[State, Entry, Any], None]] = {
    "attention.requested": on_attention_requested,
}

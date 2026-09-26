"""One `confirm.*` entry as a line of the gate's trace: which half of it, and who was asked."""

from __future__ import annotations

from pinecall.types import GateKind, GateLine
from pinecall_protocol import events


# The three confirm events carry the same four fields the gate is judged on; only the reason and
# the words differ, and no check reads those. The side effect is left unset: the log does not
# carry it, and the check fills it in from the registry the agent declared to.
def gate_line(seq: int, kind: GateKind, data: object) -> GateLine:
    """One `confirm.*` payload as a line of the gate's trace."""
    if not isinstance(data, events.ConfirmRequest | events.ConfirmGranted | events.ConfirmDeclined):
        raise TypeError(f"a {kind} entry carried a {type(data).__name__}")
    return GateLine(
        seq=seq, kind=kind, call_id=data.call_id, tool=data.tool, audience=data.audience
    )

"""The log's entry is the wire's envelope, one shape end to end; log/ names it here, once."""

import time

from pinecall_protocol import WireModel, encode
from pinecall_protocol.envelope import Entry
from pinecall_protocol.registry import EPHEMERAL_EVENTS

__all__ = ["Entry", "ephemeral_by_default", "unstored"]

# An entry that never entered a log has seq 0: every log numbers from 1, so 0 reads as "this was
# never written down".
UNSTORED = 0


# The registry knows which events are interim by nature: transcripts as they form, the VAD tick,
# the replay markers. A caller may still mark any entry ephemeral itself; this is the default.
def ephemeral_by_default(type: str) -> bool:
    """Whether the protocol lets a store forget this event and a slow reader miss it."""
    return type in EPHEMERAL_EVENTS


# Two doors build one: the app socket, for a frame that named no agent, and /v1/attach, for the
# verbs it cannot run yet. Both are answering a caller about a log neither could write to.
def unstored(type: str, event: WireModel, agent: str = "") -> Entry:
    """An entry a socket hears and no log keeps: no log could be told which one, or none should.

    The agent is named when the socket has to route it — a dev.request is for one agent's process
    — and left empty for a frame that belonged to no agent at all."""
    return Entry(
        seq=UNSTORED,
        ts=time.time(),
        call=None,
        agent=agent,
        type=type,
        ephemeral=True,
        data=encode(event),
    )

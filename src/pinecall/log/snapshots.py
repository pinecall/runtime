"""One reduction per call per seq, memoised for the whole process, so a hundred readers cost one."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from pinecall.log.reduce import reduce
from pinecall.log.replay import whole
from pinecall.log.store import Store
from pinecall_protocol.state import State


@dataclass(frozen=True)
class Snapshot:
    """A call as it stands: the state, the seq it was folded to, and whether it is still going."""

    state: State
    # The last durable seq: the cursor a stream resumes from, and what the door answers.
    last_seq: int
    # The store's seq when this was folded, ephemerals counted: what the memo is checked against.
    at_seq: int
    live: bool


# How many calls' states one gateway keeps folded. A State is a few kilobytes; a thousand of
# them is what a busy floor and its readers look at, and the oldest goes when one more arrives,
# so a scan of every call id there ever was leaves a thousand behind and not all of them.
AT_MOST = 1024


# The memo is keyed by the seq the log had when it was made, which is the only thing that can
# invalidate it: a log is append-only, so an unchanged latest_seq means an unchanged state. Asking
# the store for that number is one indexed read; folding the log again is the whole log. The seq
# kept is the STORE's, ephemerals counted, because that is the number it is compared against:
# keeping the state's own — the last durable one — meant a live call, whose head moves on every
# interim transcript, never once hit the memo (2026-09-26).
class Snapshots:
    """One reduction per call per seq, for the whole process. Nothing here survives a restart."""

    def __init__(self, store: Store, at_most: int = AT_MOST) -> None:
        self._store = store
        self._at_most = at_most
        self._memo: OrderedDict[str, Snapshot] = OrderedDict()

    async def of(self, call: str) -> Snapshot | None:
        """The call's state, from the memo when the log has not moved. None: no such call."""
        last_seq = await self._store.latest_seq(call)
        if last_seq == 0:
            return None
        memoed = self._memo.get(call)
        if memoed is not None and memoed.at_seq == last_seq:
            self._memo.move_to_end(call)
            return memoed
        state = reduce(await whole(self._store, call))
        made = Snapshot(
            state=state, last_seq=state.seq, at_seq=last_seq, live=state.status != "ended"
        )
        self._memo[call] = made
        self._memo.move_to_end(call)
        while len(self._memo) > self._at_most:
            self._memo.popitem(last=False)
        return made

    @property
    def kept(self) -> int:
        """How many calls' states are folded right now."""
        return len(self._memo)

"""One reduction per call per seq, memoised for the whole process, so a hundred readers cost one."""

from __future__ import annotations

from dataclasses import dataclass

from pinecall.log.reduce import reduce
from pinecall.log.replay import whole
from pinecall.log.store import Store
from pinecall_protocol.state import State


@dataclass(frozen=True)
class Snapshot:
    """A call as it stands: the state, the seq it was folded to, and whether it is still going."""

    state: State
    last_seq: int
    live: bool


# The memo is keyed by the seq the log had when it was made, which is the only thing that can
# invalidate it: a log is append-only, so an unchanged latest_seq means an unchanged state. Asking
# the store for that number is one indexed read; folding the log again is the whole log.
class Snapshots:
    """One reduction per call per seq, for the whole process. Nothing here survives a restart."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._memo: dict[str, Snapshot] = {}

    async def of(self, call: str) -> Snapshot | None:
        """The call's state, from the memo when the log has not moved. None: no such call."""
        last_seq = await self._store.latest_seq(call)
        if last_seq == 0:
            return None
        memoed = self._memo.get(call)
        if memoed is not None and memoed.last_seq == last_seq:
            return memoed
        state = reduce(await whole(self._store, call))
        made = Snapshot(state=state, last_seq=state.seq, live=state.status != "ended")
        self._memo[call] = made
        return made

"""GET /v1/calls/{id}/state: the reduced state, memoised per call, projected at this sink."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinecall.api.agents.registry import RegistryDep
from pinecall.api.calls.log_sink import (
    ReaderDep,
    declared_by,
    refuse_another_call,
    refuse_another_org,
)
from pinecall.api.deps import SnapshotsDep, StoreDep
from pinecall.log.projection import project_state
from pinecall.types.json import JsonObject
from pinecall_protocol import encode

router = APIRouter()


# The reader is the sink's, decided from what the caller IS and never from anything it sent: one
# rule for the page, the stream, the attach socket and this. A widget that could name its own
# projection would be a widget that could name the tenant's.
@router.get("/v1/calls/{call}/state")
async def call_state(
    call: str,
    reader: ReaderDep,
    registry: RegistryDep,
    snapshots: SnapshotsDep,
    store: StoreDep,
) -> JsonObject:
    """The whole log, folded and projected. `last_seq` is the cursor a stream resumes from."""
    refuse_another_call(reader, call)
    await refuse_another_org(reader, store, call, "")
    snapshot = await snapshots.of(call)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"no log for call {call}")
    return {
        "state": project_state(
            encode(snapshot.state),
            reader.projection,
            declared_by(registry, snapshot.state.agent),
            reader.viewer,
        ),
        "last_seq": snapshot.last_seq,
        "live": snapshot.live,
    }

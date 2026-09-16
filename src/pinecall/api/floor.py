"""The org's floor: its calls across every agent, and the stream of that floor changing."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from pinecall.api._deps import LogsDep, SnapshotsDep, StoreDep
from pinecall.api.agents.registry import RegistryDep
from pinecall.api.calls.listing import A_SCREENFUL, a_line
from pinecall.api.calls.sink import ProjectDep, ReaderDep, sse
from pinecall.auth.corner import corner_of
from pinecall_protocol.rest import SessionLine, SessionList

router = APIRouter()

# An org's stream is the tenant's: a room token reads its one call and nothing across an org.
A_KEY_READS_THE_ORG = "an org's events are read with a key"


# The same rows GET /v1/agents/{slug}/sessions draws, across every agent the org holds: what a
# floor's Sessions screen lists before anybody picks an agent. Newest first, off the head rows.
@router.get("/v1/sessions")
async def sessions(
    reader: ReaderDep,
    registry: RegistryDep,
    store: StoreDep,
    snapshots: SnapshotsDep,
    limit: Annotated[int, Query(ge=1, le=200)] = A_SCREENFUL,
) -> SessionList:
    """The org's newest calls, each folded to the row a list draws, projected at this sink."""
    if reader.key is None:
        raise HTTPException(403, A_KEY_READS_THE_ORG)
    lines: list[SessionLine] = []
    whose = corner_of(reader.key)
    for call in await store.calls_of(whose.org, limit, whose.env, whose.holder or ""):
        snapshot = await snapshots.of(call)
        if snapshot is not None:
            lines.append(a_line(call, snapshot, reader, registry))
    return SessionList(calls=lines)


# Live only, and no cursor: the feed is the moments a floor changes shape — an agent held or let
# go, a call arriving, up, over — tapped off every log the org owns as they are written. Each of
# them is an entry of some log already, with its own seq there; what to resume from is that log.
@router.get("/v1/events", response_model=None)
async def events(reader: ReaderDep, logs: LogsDep, project: ProjectDep) -> StreamingResponse:
    """The org's floor as it changes, as SSE, from now on."""
    if reader.key is None:
        raise HTTPException(403, A_KEY_READS_THE_ORG)
    return sse(logs.feed(reader.key.org).subscribe(), project, reader, ends_at=None)

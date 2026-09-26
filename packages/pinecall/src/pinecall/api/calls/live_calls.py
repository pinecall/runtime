"""The org's floor: its calls across every agent, and the stream of that floor changing."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from pinecall.api.calls.listing import A_SCREENFUL, BEFORE, LIMIT, WORDS, page_of_calls
from pinecall.api.calls.log_sink import ProjectDep, ReaderDep, sse
from pinecall.api.deps import CallIndexDep, LogsDep, RegistryDep, SnapshotsDep
from pinecall.api.sse import ClosingDep
from pinecall.log.store.call_index import Wanted
from pinecall.types import Channel
from pinecall_protocol.rest import SessionList

router = APIRouter()

# An org's stream is the tenant's: a room token reads its one call and nothing across an org.
A_KEY_READS_THE_ORG = "an org's events are read with a key"


# The same rows GET /v1/agents/{slug}/sessions draws, across every agent the org holds: what a
# floor's Sessions screen lists before anybody picks an agent, filtered and paged by the same
# words as the agent's own door, off the call index.
@router.get("/v1/sessions")
async def sessions(
    reader: ReaderDep,
    registry: RegistryDep,
    index: CallIndexDep,
    snapshots: SnapshotsDep,
    limit: Annotated[int, LIMIT] = A_SCREENFUL,
    q: Annotated[str | None, WORDS] = None,
    agent: str | None = None,
    channel: Channel | None = None,
    before: Annotated[str | None, BEFORE] = None,
) -> SessionList:
    """The org's newest calls that match, each folded to the row a list draws."""
    if reader.key is None:
        raise HTTPException(403, A_KEY_READS_THE_ORG)
    wanted = Wanted(agent=agent or None, channel=channel, q=q or None, before=before)
    return await page_of_calls(reader, registry, index, snapshots, wanted, limit)


# Live only, and no cursor: the feed is the moments a floor changes shape — an agent held or let
# go, a call arriving, up, over — tapped off every log the org owns as they are written. Each of
# them is an entry of some log already, with its own seq there; what to resume from is that log.
@router.get("/v1/events", response_model=None)
async def events(
    reader: ReaderDep, logs: LogsDep, project: ProjectDep, closing: ClosingDep
) -> StreamingResponse:
    """The org's floor as it changes, as SSE, from now on."""
    if reader.key is None:
        raise HTTPException(403, A_KEY_READS_THE_ORG)
    return sse(logs.feed(reader.key.org).subscribe(), project, reader, closing, ends_at=None)

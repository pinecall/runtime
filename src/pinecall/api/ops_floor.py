"""The box's floor: every org's floor changing, on one stream, for the operator."""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from pinecall.api._deps import LogsDep
from pinecall.api._operator import an_operator
from pinecall.api.calls.sink import a_stream
from pinecall.log.entry import Entry
from pinecall.log.writers import Logs
from pinecall.types.json import JsonObject
from pinecall_protocol import encode
from pinecall_protocol.rest import BoxEvent

# The same gate every /v1/ops door takes: the box's own key, or a person the box made an operator.
# What reads it is whatever serves the box as a whole and must hear every org at once — a
# notifier, a wallboard — where one org's key would need a stream per org it could never list.
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])


# Live only, like an org's own stream, and no cursor: each frame's id is the seq of its entry in
# its own log, so ids from two calls interleave and a reconnect resumes from now. Nothing is
# projected: the operator is the box's, and what the store holds was masked when it was written.
@operator.get("/events", response_model=None)
async def events(logs: LogsDep) -> StreamingResponse:
    """Every org's floor as it changes, as SSE, each frame naming the org it happened in."""
    return a_stream(_owned(logs, logs.box().subscribe()))


async def _owned(
    logs: Logs, entries: AsyncIterator[Entry]
) -> AsyncIterator[tuple[Entry, JsonObject]]:
    """Each entry with the org whose log it is. One nobody owns any more is skipped."""
    async for entry in entries:
        org = await logs.owner(entry.call, entry.agent)
        if org is not None:
            said = BoxEvent.model_validate({"org": org, "entry": encode(entry)})
            yield entry, encode(said)

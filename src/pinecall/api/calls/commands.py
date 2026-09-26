"""The worker's door onto what the app said about its call: one command per frame, as they come."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from starlette.responses import StreamingResponse

from pinecall.api.calls.log_sink import PING, PING_SECONDS, SSE, SSE_HEADERS, an_sse_frame, paced
from pinecall.api.calls.worker_writes import refuse_another_orgs_call
from pinecall.api.deps import AppKeyDep
from pinecall.api.live import LiveDep
from pinecall_protocol import Command, encode

router = APIRouter()

# Nothing was ever opened under this id here, so nothing can be said about it either. 404 and not
# an empty stream: a worker that opened its call somewhere else must learn that now, not in the
# silence of a call whose prompt never arrives.
NOT_SERVED = "this gateway serves no call {call!r}: open it with POST /v1/calls first"


# The other half of api/calls/tools.py: there a worker asks the app to run something, here the app
# tells the worker's call what to do. Both travel through the app socket this process holds, and
# neither is a new vocabulary — the frames are the protocol's own command envelope.
@router.get("/v1/calls/{call}/commands")
async def commands(call: str, key: AppKeyDep, live: LiveDep) -> StreamingResponse:
    """Every command the app sends for this call, in order, until the call is sealed."""
    # A tenant's worker reads its own org's calls: reading is also CONSUMING the queue, so a key
    # of another org that named this id would take the commands off the worker running it.
    refuse_another_orgs_call(live, key, call)
    waiting = live.commands(call)
    if waiting is None:
        raise HTTPException(status_code=404, detail=NOT_SERVED.format(call=call))
    return StreamingResponse(_body(waiting), media_type=SSE, headers=SSE_HEADERS)


# No `retry:` and no `id:`: the reader is the worker, not a browser, and a command has no seq to
# resume from. A worker that loses the stream has lost the call it was reading it for.
async def _body(waiting: asyncio.Queue[Command | None]) -> AsyncIterator[str]:
    """A frame per command, a comment when the call is quiet, and the end when it is sealed."""
    async for command in paced(_taken(waiting), PING_SECONDS):
        if command is None:
            yield PING
            continue
        yield an_sse_frame(command.type, encode(command))


async def _taken(waiting: asyncio.Queue[Command | None]) -> AsyncIterator[Command]:
    """The queue as a stream: None is the call ending, and it is the only way this stops."""
    while (command := await waiting.get()) is not None:
        yield command

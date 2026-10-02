"""A call's recording as a player reads it: plain, or sealed and opened a range at a time."""

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse, StreamingResponse

from pinecall.domain.errors import NotFound
from pinecall.gateway import _deps
from pinecall.gateway._deps import CallReaderDep, GatewayDep
from pinecall.gateway._gateway import Gateway
from pinecall.log import queries
from pinecall.process.recordings import SEALED_FILE, Fetched, recordings_of, served_sealed
from pinecall.process.sealed_audio import on_disk
from pinecall.tenancy import recording_keys

router = APIRouter()


NOT_RECORDED = "call {call} kept no recording"


NOT_HERE = "the recording of {call} is at {path} on the box that took the call, not on this one"


# From the bucket once the worker moved it there, else the file the recorder wrote on this disk;
# a sealed one is opened by the call's own key, the range asked for and no more.
@router.get("/v1/calls/{call}/recording", response_model=None)
async def recording(
    call: str,
    reading: CallReaderDep,
    gateway: GatewayDep,
    byte_range: Annotated[str | None, Header(alias="range")] = None,
) -> FileResponse | StreamingResponse:
    """The call's audio, with byte ranges so a player can seek."""
    await _deps.check_readable(gateway, reading, call)
    await _deps.record_read(gateway, reading, call, "recording")
    state = await gateway.logs.reading(call).snapshot()
    summary = next(
        (
            item
            for item in reversed(await gateway.logs.store.whole(call))
            if item.type == "call.summary"
        ),
        None,
    )
    pointer = None if summary is None else summary.data.get("recording")
    if state.seq == 0 or not isinstance(pointer, str) or not pointer:
        raise NotFound(NOT_RECORDED.format(call=call))
    kept = await queries.scope_of_call(gateway.connections.pool, call)
    path = Path(pointer)
    if kept is not None and kept.scope is not None:
        fetched = await _fetched(gateway, kept.scope.org, call, path, byte_range)
        if fetched is not None:
            headers = {
                **fetched.headers,
                "content-disposition": f'attachment; filename="{call}.ogg"',
            }
            return StreamingResponse(
                fetched.body, status_code=fetched.status, headers=headers, media_type="audio/ogg"
            )
    if path.name == SEALED_FILE or not await asyncio.to_thread(path.is_file):
        raise NotFound(NOT_HERE.format(call=call, path=pointer))
    return FileResponse(path, media_type="audio/ogg", filename=f"{call}.ogg")


# A sealed recording is in the bucket, or on this disk where its upload failed; a plain one is
# the bucket's to answer, and a file on this disk is served as a file.
async def _fetched(
    gateway: Gateway, org: str, call: str, path: Path, byte_range: str | None
) -> Fetched | None:
    connections = gateway.connections
    stored = recordings_of(connections.settings, connections.http)
    if path.name != SEALED_FILE:
        return await stored.fetch(org, call, byte_range)
    key = await recording_keys.key_of(connections.pool, connections.vault, call)
    kept = await stored.sealed(org, call) or await on_disk(call, path)
    return None if key is None or kept is None else served_sealed(kept, key, byte_range)

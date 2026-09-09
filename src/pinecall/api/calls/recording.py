"""GET /v1/calls/{call}/recording: the audio the call's summary points at, served off this box."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse

from pinecall.api._deps import StoreDep
from pinecall.api.calls.sink import ReaderDep, refuse_another_call
from pinecall.log.replay import whole

router = APIRouter()

# The pointer travels in the call's summary and nowhere else: the worker composes it
# (worker/recordings.py) and the log states it there, once, near the end.
THE_SUMMARY = "call.summary"
AUDIO = "audio/ogg"

NO_SUCH_CALL = "no log for call {call}"
NOT_SEALED = "call {call} has no {summary} yet: the recording is stated when the call ends"
NOT_RECORDED = "call {call} kept no recording: its {summary} points at none"
NOT_HERE = "the recording of {call} is at {path} on the box that took the call, and not on this one"


# The same reader every log door has: an API key is the tenant, a call token reads its own call
# and no other. The file is looked for where the pointer says, relative to this process, which is
# where it is when the gateway and the worker share a box — the laptop, or ours.
@router.get("/v1/calls/{call}/recording")
async def recording(call: str, reader: ReaderDep, store: StoreDep) -> FileResponse:
    """The call's audio.ogg, with byte ranges honoured so a player can seek."""
    refuse_another_call(reader, call)
    entries = await whole(store, call)
    if not entries:
        raise HTTPException(404, NO_SUCH_CALL.format(call=call))
    summary = next((entry for entry in reversed(entries) if entry.type == THE_SUMMARY), None)
    if summary is None:
        raise HTTPException(404, NOT_SEALED.format(call=call, summary=THE_SUMMARY))
    pointer = summary.data.get("recording")
    if not isinstance(pointer, str) or not pointer:
        raise HTTPException(404, NOT_RECORDED.format(call=call, summary=THE_SUMMARY))
    path = Path(pointer)
    if not path.is_file():
        raise HTTPException(404, NOT_HERE.format(call=call, path=pointer))
    return FileResponse(path, media_type=AUDIO, filename=f"{call}.ogg")

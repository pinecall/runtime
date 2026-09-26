"""The doors onto a log: what a reader may ask, the agent's own log, a supervisor's socket."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Response, WebSocket, WebSocketDisconnect
from starlette.responses import StreamingResponse
from starlette.status import HTTP_204_NO_CONTENT

from pinecall.api.calls.log_sink import (
    AcceptDep,
    CursorDep,
    FilterDep,
    LimitDep,
    ProjectDep,
    ProjectedPage,
    ReaderDep,
    another_orgs,
    get_reader_or_none,
    is_sealed,
    page,
    refuse_another_call,
    refuse_another_org,
    sse,
    wants_sse,
)
from pinecall.api.calls.supervise.aiming import (
    STEERS,
    QueueingDep,
    VerbRefused,
    aim_verb,
    parse_verb,
)
from pinecall.api.deps import (
    KeysDep,
    LogsDep,
    SettingsDep,
    SnapshotsDep,
    StoreDep,
)
from pinecall.auth.bearer import POLICY_VIOLATION, close_reason
from pinecall.auth.keys import cannot_open
from pinecall.auth.scopes import Reader
from pinecall.log.entry import Entry, ephemeral_entry
from pinecall.log.filters import EVERYTHING
from pinecall.log.store import DEFAULT_LIMIT
from pinecall_protocol import ProtocolError, encode
from pinecall_protocol.events import ErrorEvent
from pinecall_protocol.registry import TERMINAL_EVENT

router = APIRouter()


# A frame this socket could not read as one of the six verbs. The pydantic sentence rides with it,
# so a desk with a typo in its JSON learns which field, not just that something was wrong.
BAD_VERB = "bad_verb"

# A verb this socket read and will not apply: the wrong call, a call that ended, nothing running
# here. The sentence is aiming.py's own, so the socket and POST /v1/calls/{call}/verbs refuse in
# the same words.
VERB_REFUSED = "verb_refused"

# ── one call's log ──────────────────────────────────────────────────────────────


# response_model=None: one URL, two flavours — FastAPI cannot make a model out of "or a stream".
@router.get("/v1/calls/{call}/events", response_model=None)
async def events(
    call: str,
    reader: ReaderDep,
    store: StoreDep,
    logs: LogsDep,
    project: ProjectDep,
    cursor: CursorDep,
    filter: FilterDep,
    accept: AcceptDep = None,
    limit: LimitDep = DEFAULT_LIMIT,
) -> Response | StreamingResponse | ProjectedPage:
    """The call's entries above the cursor: SSE when the reader asked for it, a page otherwise."""
    refuse_another_call(reader, call)
    await refuse_another_org(reader, store, call, "")
    over = await is_sealed(store, call)
    if over and cursor >= await store.latest_seq(call):
        # Nothing more will ever be true of this call and the reader has all of it. An empty page
        # would be a promise to come back, and 200 with `live: false` is what the JSON flavour
        # says; a stream has no body to say it in, so 204 says it once and the reader stops.
        return Response(status_code=HTTP_204_NO_CONTENT)
    if wants_sse(accept):
        # logs.reading() hands back the live log when this process is writing the call, so the
        # stream goes on into the fanout; for a call nobody here is writing it reads the store and
        # then waits, which is what a reader of another gateway's call should do.
        return sse(logs.reading(call).stream(after=cursor, filter=filter), project, reader)
    entries: list[Entry] = await store.since(call, after=cursor, limit=limit)
    # The filter is applied here, at the sink, and the seq of what survives is untouched: a reader
    # that narrows its view still holds cursors it can hand back to this same endpoint.
    kept = [entry for entry in entries if filter.passes(entry)]
    return page(entries, kept, not over, project, reader)


# ── the agent's own log ─────────────────────────────────────────────────────────


# `live` is always true here and there is no 204: an agent's log ends when the agent stops
# existing, which is not an event. A reader of this URL is meant to hold it open for ever.
# response_model=None: this route answers a page or a stream, and only Accept knows which.
@router.get("/v1/agents/{slug}/calls", response_model=None)
async def calls(
    slug: str,
    reader: ReaderDep,
    store: StoreDep,
    logs: LogsDep,
    project: ProjectDep,
    cursor: CursorDep,
    filter: FilterDep,
    accept: AcceptDep = None,
    limit: LimitDep = DEFAULT_LIMIT,
) -> StreamingResponse | ProjectedPage:
    """What happened to this agent outside any call: registered, configured, an error, a call."""
    refuse_another_call(reader, None)
    await refuse_another_org(reader, store, None, slug)
    if wants_sse(accept):
        # ends_at None: no event terminates this log, so the body closes when the reader leaves.
        stream = logs.reading_agent(slug).stream(after=cursor, filter=filter)
        return sse(stream, project, reader, ends_at=None)
    entries: list[Entry] = await store.agent_since(slug, after=cursor, limit=limit)
    kept = [entry for entry in entries if filter.passes(entry)]
    return page(entries, kept, True, project, reader)


# ── a supervisor's socket ───────────────────────────────────────────────────────


# The socket reads the call downwards and takes the six verbs upwards. Both halves are the
# supervisor's, and the verbs go through the very same aiming.aimed() that POST
# /v1/calls/{call}/verbs calls: one set of checks, whichever door a desk knocked at.
@router.websocket("/v1/attach")
async def attach(
    websocket: WebSocket,
    keys: KeysDep,
    settings: SettingsDep,
    logs: LogsDep,
    store: StoreDep,
    project: ProjectDep,
    snapshots: SnapshotsDep,
    live: QueueingDep,
    call: Annotated[str, Query()],
    token: Annotated[str | None, Query()] = None,
    after: Annotated[int, Query(ge=0)] = 0,
) -> None:
    """One supervisor, one call: the entries down the socket, and the verbs back up it."""
    reader = await get_reader_or_none(websocket, keys, settings, token)
    if reader is None or (reader.call is not None and reader.call != call):
        await websocket.close(code=POLICY_VIOLATION)
        return
    if await another_orgs(reader, store, call, ""):
        await websocket.close(code=POLICY_VIOLATION)
        return
    await websocket.accept()
    # A key on this socket reads the call and steers it, so it is asked for the second: a person
    # who may only watch is told so in the one sentence, and the socket closes.
    if reader.key is not None and (closed := cannot_open(reader.key, STEERS)) is not None:
        await websocket.close(code=POLICY_VIOLATION, reason=close_reason(closed))
        return
    tail = asyncio.ensure_future(_tail(websocket, logs, project, reader, call, after))
    try:
        # receive(), not receive_json(): a frame that is not JSON at all is answered by name here
        # rather than closing the socket under a desk that mistyped one message.
        while (frame := await websocket.receive())["type"] != "websocket.disconnect":
            said = await _verb(live, store, snapshots, reader, call, frame.get("text"))
            if said is not None:
                await websocket.send_json(encode(said))
    except WebSocketDisconnect:
        pass
    finally:
        tail.cancel()


# The answer to an inbound frame is either nothing — the verb is on the worker's queue and the
# log is where its effect shows up — or one error entry no log keeps: every log numbers from 1, so
# seq 0 reads as "this was never written down", the same shape the app socket uses.
async def _verb(
    live: QueueingDep,
    store: StoreDep,
    snapshots: SnapshotsDep,
    reader: Reader,
    call: str,
    frame: str | None,
) -> Entry | None:
    """One frame as a verb, aimed at the call. None means it went; an entry says why it did not."""
    try:
        said = parse_verb(frame)
    except ProtocolError as malformed:
        return _an_error(BAD_VERB, str(malformed))
    try:
        await aim_verb(live, store, snapshots, reader, call, said)
    except VerbRefused as refused:
        return _an_error(VERB_REFUSED, refused.detail)
    return None


def _an_error(code: str, why: str) -> Entry:
    """One refusal as the entry the socket sends back, with no seq because no log kept it."""
    return ephemeral_entry("error", ErrorEvent(code=code, message=why, recoverable=True))


async def _tail(
    websocket: WebSocket,
    logs: LogsDep,
    project: ProjectDep,
    reader: Reader,
    call: str,
    after: int,
) -> None:
    """The call's log down the socket, entry by entry, ending where the call does."""
    async for entry in logs.reading(call).stream(after=after, filter=EVERYTHING):
        said = project(entry, reader)
        if said is not None:
            await websocket.send_json(said)
        if entry.type == TERMINAL_EVENT:
            return

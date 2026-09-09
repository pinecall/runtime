"""The two doors a session knocks at around a turn: fill its markers, and remember the call."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from pinecall.api._deps import FillingDep, KeyDep
from pinecall.api._live import Live, LiveDep
from pinecall.api.calls.events import NOT_OPEN
from pinecall.types import Marker, MarkerName
from pinecall_protocol.rest import Fill, FillRequest, Fills, Remembered

router = APIRouter()


# Worker-only, the way /v1/agents/{slug}/provider-keys is: an API key IS its org, and a call
# another org opened is a 404 in the same words a call nobody opened gets — that it exists at
# all is not the asker's business. The gateway writes memory.ops and docs.sources on the call's
# log itself; the worker holds no database and learns no seq. docs/decisions/memory.md.
@router.post("/v1/calls/{call}/fill")
async def fill(
    call: str, said: FillRequest, key: KeyDep, live: LiveDep, filling: FillingDep
) -> Fills:
    """This turn's fills for a call this gateway serves: every marker's line to its text."""
    _the_orgs_open_call(live, key.org, call)
    started = time.perf_counter()
    # The worker sends the marker's name and payload and never its line: the line is the
    # session's key and the payload is the ask, so the line is rebuilt here for the service and
    # dropped again for the answer.
    markers = [_a_marker(one.name, one.payload) for one in said.markers]
    answered = await filling.fill(call, said.query, markers, said.speech_id)
    return Fills(
        fills=[
            Fill(name=marker.name, payload=marker.payload, text=answered.get(marker.line, ""))
            for marker in markers
        ],
        took_ms=(time.perf_counter() - started) * 1000,
    )


@router.post("/v1/calls/{call}/remember")
async def remember(call: str, key: KeyDep, live: LiveDep, filling: FillingDep) -> Remembered:
    """The call's turns off its own log through memory, and memory.ops written; how many ops."""
    _the_orgs_open_call(live, key.org, call)
    started = time.perf_counter()
    ops = await filling.remember(call)
    return Remembered(ops=ops, took_ms=(time.perf_counter() - started) * 1000)


def _the_orgs_open_call(live: Live, org: str, call: str) -> None:
    """404 in the events door's words: not open here, or not this org's — one sentence for both."""
    opened = live.the_call(call)
    if opened is None or opened.org != org:
        raise HTTPException(status_code=404, detail=NOT_OPEN.format(call=call))


def _a_marker(name: MarkerName, payload: str) -> Marker:
    """The marker as the view would have written it, so the fill's key is the line as written."""
    line = f"<!-- {name}: {payload} -->" if payload else f"<!-- {name}: -->"
    return Marker(name=name, payload=payload, line=line)

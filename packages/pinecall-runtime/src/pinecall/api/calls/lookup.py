"""The two doors a session knocks at around a turn: run one lookup, and remember the call."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from pinecall.api.calls.worker_writes import NOT_OPEN
from pinecall.api.deps import AppKeyDep, LiveDep, LookupsDep
from pinecall.auth.keys import KeyRecord, is_fleet_key
from pinecall.live.calls import Live
from pinecall_protocol.rest import LookupRequest, LookupResult, Remembered

router = APIRouter()


# Worker-only, the way /v1/agents/{slug}/provider-keys is: an API key IS its org, and a call
# another org opened is a 404 in the same words a call nobody opened gets — that it exists at
# all is not the asker's business. The fleet's worker opened the call, whoever's it is, and asks
# on its behalf. The gateway writes memory.ops and docs.sources on the call's log itself; the
# worker holds no database and learns no seq. docs/decisions/memory.md.
@router.post("/v1/calls/{call}/lookup")
async def lookup(
    call: str, said: LookupRequest, key: AppKeyDep, live: LiveDep, lookups: LookupsDep
) -> LookupResult:
    """One run of recall or search for a call this gateway serves: what it found, as an object."""
    _the_orgs_open_call(live, key, call)
    started = time.perf_counter()
    output = await lookups.lookup(call, said.tool, said.input, said.speech_id)
    return LookupResult(output=dict(output), took_ms=(time.perf_counter() - started) * 1000)


@router.post("/v1/calls/{call}/remember")
async def remember(call: str, key: AppKeyDep, live: LiveDep, lookups: LookupsDep) -> Remembered:
    """The call's turns off its own log through memory, and memory.ops written; how many ops."""
    _the_orgs_open_call(live, key, call)
    started = time.perf_counter()
    ops = await lookups.remember(call)
    return Remembered(ops=ops, took_ms=(time.perf_counter() - started) * 1000)


def _the_orgs_open_call(live: Live, key: KeyRecord, call: str) -> None:
    """404 in the events door's words: not open here, or not this org's — one sentence for both."""
    opened = live.the_call(call)
    if opened is None or (opened.org != key.org and not is_fleet_key(key)):
        raise HTTPException(status_code=404, detail=NOT_OPEN.format(call=call))

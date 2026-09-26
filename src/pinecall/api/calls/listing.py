"""GET /v1/agents/{slug}/sessions: the agent's calls, one line each, off the same folded states."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from pinecall.api.agents.registry import Registry, RegistryDep
from pinecall.api.calls.log_sink import (
    ReaderDep,
    declared_by,
    refuse_another_call,
    refuse_another_org,
)
from pinecall.api.deps import CallIndexDep, SnapshotsDep, StoreDep
from pinecall.auth.request_scope import corner_of
from pinecall.auth.scopes import Reader
from pinecall.log.call_facts import CallFacts
from pinecall.log.projection import project_state
from pinecall.log.snapshots import Snapshot, Snapshots
from pinecall.log.store.call_index import CallIndex, Wanted
from pinecall.types import Channel
from pinecall.types.json import JsonObject
from pinecall_protocol import encode
from pinecall_protocol.rest import SessionLine, SessionList

router = APIRouter()

# How many calls a screen asks for when it says nothing. The same number `pinecall-runtime sessions
# list` shows, and for the same reason: it is a screenful, not a page of the store.
A_SCREENFUL = 20

# What a line says about a call: every field of the protocol's own SessionLine that the projected
# state answers for. The row's shape is protocol/schema/rest.json and never a shape computed here.
# The verdict and the flags are the call index's, not the state's.
# A list is read on a key and never on a room token: the token opens one call, not a corner.
NOT_A_KEY = "a list of calls is read on an API key, not on a call token"

OF_THE_LINE = ("call", "live", "last_seq", "score", "flags")
OF_THE_STATE = tuple(
    field.alias or name
    for name, field in SessionLine.model_fields.items()
    if name not in OF_THE_LINE
)

# The filters both list doors take, described once.
LIMIT = Query(ge=1, le=200)
WORDS = Query(
    max_length=200,
    description="the call id's start, a number's digits, the caller's name or the outcome",
)
BEFORE = Query(description="the `next` of the page before: the list continues below that call")


# The agent's own log (GET /v1/agents/{slug}/calls) says what happened to the AGENT — registered,
# configured, an error — and a call's entries are written into the call's own log, so nothing on
# that stream names a call. This is the other question, and it is a list rather than a log: which
# calls this agent handled, newest first. See docs/decisions/console.md.
@router.get("/v1/agents/{slug}/sessions")
async def sessions(
    slug: str,
    reader: ReaderDep,
    registry: RegistryDep,
    store: StoreDep,
    index: CallIndexDep,
    snapshots: SnapshotsDep,
    limit: Annotated[int, LIMIT] = A_SCREENFUL,
    q: Annotated[str | None, WORDS] = None,
    channel: Channel | None = None,
    before: Annotated[str | None, BEFORE] = None,
) -> SessionList:
    """This agent's newest calls that match, each folded to the row a list draws."""
    refuse_another_call(reader, None)
    await refuse_another_org(reader, store, None, slug)
    wanted = Wanted(agent=slug, channel=channel, q=q or None, before=before)
    return await page_of_calls(reader, registry, index, snapshots, wanted, limit)


# One page, however it was listed: the agent's door and the org's (api/calls/live_calls.py) draw the
# same rows, in the reader's corner — a developer's sandbox test calls are theirs, the telephone's
# are production's, and an admin reading a colleague's copy reads that corner.
async def page_of_calls(
    reader: Reader,
    registry: Registry,
    index: CallIndex,
    snapshots: Snapshots,
    wanted: Wanted,
    limit: int,
) -> SessionList:
    """The calls that match, a page of them folded to rows, how many match, and the cursor."""
    if reader.key is None:  # both doors refuse a token before they ask for a page
        raise HTTPException(401, NOT_A_KEY)
    whose = corner_of(reader.key)
    found = await index.found(whose.org, whose.env, whose.holder or "", wanted, limit)
    facts = await index.facts_of(found.calls)
    lines: list[SessionLine] = []
    for call in found.calls:
        snapshot = await snapshots.of(call)
        if snapshot is not None:
            lines.append(_a_line(call, snapshot, reader, registry, facts.get(call)))
    return SessionList(calls=lines, total=found.total, next=found.next)


def _a_line(
    call: str,
    snapshot: Snapshot,
    reader: Reader,
    registry: Registry,
    facts: CallFacts | None = None,
) -> SessionLine:
    """The call as this reader may see it, cut to the row a list draws, with its verdict."""
    return _line(call, snapshot, _said(snapshot, reader, registry), facts)


# The projection is applied to the whole state and the row is cut out of what comes back, so a
# caller's number is masked here by exactly the rule that masks it on the call's own state door.
def _said(snapshot: Snapshot, reader: Reader, registry: Registry) -> JsonObject:
    """The call's state as this reader may see it."""
    return project_state(
        encode(snapshot.state),
        reader.projection,
        declared_by(registry, snapshot.state.agent),
        reader.viewer,
    )


def _line(call: str, snapshot: Snapshot, said: JsonObject, facts: CallFacts | None) -> SessionLine:
    """One call as a list draws it: which call, how far the log got, the state's own fields, and
    what the index knows — how the judges answered and what to look at first."""
    row: JsonObject = {
        "call": call,
        "live": snapshot.live,
        "last_seq": snapshot.last_seq,
        "score": None if facts is None else facts.score_row,
        "flags": [] if facts is None else facts.flags,
    }
    return SessionLine.model_validate({**row, **{name: said.get(name) for name in OF_THE_STATE}})

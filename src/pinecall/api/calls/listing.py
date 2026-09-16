"""GET /v1/agents/{slug}/sessions: the agent's calls, one line each, off the same folded states."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from pinecall.api._deps import SnapshotsDep, StoreDep
from pinecall.api.agents.registry import Registry, RegistryDep
from pinecall.api.calls.sink import (
    ReaderDep,
    declared_by,
    refuse_another_call,
    refuse_another_org,
)
from pinecall.auth.corner import corner_of
from pinecall.auth.scopes import Reader
from pinecall.log.projection import project_state
from pinecall.log.snapshots import Snapshot
from pinecall.types.json import JsonObject
from pinecall_protocol import encode
from pinecall_protocol.rest import SessionLine, SessionList

router = APIRouter()

# How many calls a screen asks for when it says nothing. The same number `pinecall-runtime sessions
# list` shows, and for the same reason: it is a screenful, not a page of the store.
A_SCREENFUL = 20

# What a line says about a call: every field of the protocol's own SessionLine that the projected
# state answers for. The row's shape is protocol/schema/rest.json and never a shape computed here.
OF_THE_LINE = ("call", "live", "last_seq")
OF_THE_STATE = tuple(
    field.alias or name
    for name, field in SessionLine.model_fields.items()
    if name not in OF_THE_LINE
)


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
    snapshots: SnapshotsDep,
    limit: Annotated[int, Query(ge=1, le=200)] = A_SCREENFUL,
) -> SessionList:
    """This agent's newest calls, each folded to the row a list draws, projected at this sink."""
    refuse_another_call(reader, None)
    await refuse_another_org(reader, store, None, slug)
    assert reader.key is not None  # refuse_another_call: a token reads one call, never a list
    # This corner's calls and nobody else's: a developer's sandbox test calls are theirs, the
    # telephone's are production's, and an admin reading a colleague's copy reads that corner.
    whose = corner_of(reader.key)
    newest = await store.calls_of(whose.org, limit, whose.env, whose.holder or "", slug)
    lines: list[SessionLine] = []
    for call in newest:
        snapshot = await snapshots.of(call)
        if snapshot is not None:
            lines.append(a_line(call, snapshot, reader, registry))
    return SessionList(calls=lines)


# One row, however it was listed: the agent's door and the org's (api/floor.py) draw the same line.
def a_line(call: str, snapshot: Snapshot, reader: Reader, registry: Registry) -> SessionLine:
    """The call as this reader may see it, cut to the row a list draws."""
    return _line(call, snapshot, _said(snapshot, reader, registry))


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


def _line(call: str, snapshot: Snapshot, said: JsonObject) -> SessionLine:
    """One call as a list draws it: which call, how far the log got, and the state's own fields."""
    row: JsonObject = {"call": call, "live": snapshot.live, "last_seq": snapshot.last_seq}
    return SessionLine.model_validate({**row, **{name: said.get(name) for name in OF_THE_STATE}})

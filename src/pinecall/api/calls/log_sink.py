"""The sink every log reader drains through: the door, the cursor, the JSON page, the SSE body."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query
from starlette.requests import HTTPConnection
from starlette.responses import StreamingResponse

from pinecall._settings import Settings
from pinecall.api.agents.registry import Registry, RegistryDep
from pinecall.api.deps import SCOPE_OF_THE_DOOR, KeysDep, MembersDep, SettingsDep
from pinecall.auth.bearer import bearer_of
from pinecall.auth.corner import corner_of
from pinecall.auth.keys import KeyRecord, Keys, is_the_fleets, not_opening
from pinecall.auth.scopes import LivekitKeys, Reader, a_reader, is_a_jwt, secret_for
from pinecall.auth.world import as_asked
from pinecall.log.entry import Entry
from pinecall.log.filters import Filter
from pinecall.log.projection import project_entry
from pinecall.log.store import DEFAULT_LIMIT, Store
from pinecall.log.store.call_index import CallCorner, CallIndex
from pinecall.types.agent import AgentConfig
from pinecall.types.json import JsonObject
from pinecall_protocol import WireModel, encode
from pinecall_protocol.registry import TERMINAL_EVENT

# What a reader is allowed to see of one entry, applied HERE and nowhere above: None when the
# entry never leaves at all, and otherwise the wire-shaped dict a sink sends.
type Project = Callable[[Entry, Reader], JsonObject | None]

SSE = "text/event-stream"

# How long a browser waits before reconnecting, in milliseconds. One second: an EventSource that
# reconnects with its Last-Event-ID loses nothing, so there is no reason to make it wait.
RETRY_MS = 1000

# A comment frame every 25 s. It is not a heartbeat the protocol knows about — it is bytes, so that
# a proxy with a 30 s idle timeout does not cut a stream that is simply on a quiet call.
PING_SECONDS = 25.0

# SSE has no body to put a status in, so nginx and friends are told here not to hold onto one.
SSE_HEADERS = {"cache-control": "no-store", "connection": "keep-alive", "x-accel-buffering": "no"}

# What a quiet stream says so the connection is seen to be alive: a comment, which no reader parses.
PING = ": ping\n\n"


# ── the door ────────────────────────────────────────────────────────────────────


# The key rides the Authorization header everywhere else. It may also ride ?token= HERE and only
# here, because EventSource cannot set a header — a browser reading its own call has no other way
# to say who it is. A URL ends up in an access log, which is why the app socket refuses the query
# string outright and why the tokens this accepts are the short-lived ones.
async def reading(
    connection: HTTPConnection, keys: Keys, settings: Settings, token: str | None
) -> Reader | None:
    """Who is reading: the Bearer key, or ?token= for a browser. None means nobody we know."""
    header = bearer_of(connection.headers)
    if header:
        return await a_reader(header, keys, _a_secret(settings))
    # A KEY in the query string is refused here and not merely discouraged: this is the one door
    # that reads a bearer out of a URL, and a URL is written down — the access log, the referrer,
    # the history of whatever browser followed it. A room token is short-lived and reads one call;
    # a key is the whole tenant, for as long as nobody revokes it. The paragraph above has said so
    # since this door was written, and the door took either until 2026-09-20 (found against
    # production: `GET /v1/calls/{call}/events?token=pc_…` answered the org's log).
    return await a_reader(token, keys, _a_secret(settings)) if token and is_a_jwt(token) else None


# A token bound to a call reads that call and nothing else — not another call's log, not another
# call's state, and not an agent's own log, which is the tenant's history and names every caller
# the agent ever answered. 403 and never 404: whether a call by that id exists is not a guest's
# business. Both routes that can be asked about one call ask this, so there is one rule.
NOT_YOURS = "this token does not read that call"


def refuse_another_call(reader: Reader, call: str | None) -> None:
    """Refuse a reader whose token was minted for some other log than the one it asked for."""
    if reader.call is not None and reader.call != call:
        raise HTTPException(status_code=403, detail=NOT_YOURS)


# An API key IS the org, and a log IS one org's: the call a key opened, the agent a key registered.
# A key reading another org's log is 403 in these words and never 404 — whether that call exists
# is not another tenant's business. A log nobody has claimed yet — an agent that never registered,
# a call nobody opened — is empty, and reading empty leaks nothing. Every door that reads a log
# by a key asks this, so there is one rule — and one exception, the fleet's key: the box's worker
# reads back the very calls it writes, whoever's they are, to put them on the room's DataChannel.
NOT_YOUR_ORGS = "this key does not read that org's log"


async def another_orgs(reader: Reader, store: Store, call: str | None, agent: str) -> bool:
    """Whether this key's org is not the one whose log this is. The question, without the answer:
    a socket has no 403 to raise and a verb answers in its own words."""
    if reader.key is None or is_the_fleets(reader.key):
        return False
    owner = await store.owner(call, agent)
    return owner is not None and owner != reader.key.org


async def refuse_another_org(reader: Reader, store: Store, call: str | None, agent: str) -> None:
    """Refuse a key whose org does not own the log it asked for. A token has its own gate."""
    if await another_orgs(reader, store, call, agent):
        raise HTTPException(status_code=403, detail=NOT_YOUR_ORGS)


# One sentence for a call nobody wrote and for a call of another org, world or corner: whether it
# exists is not the asker's business, the same rule the log doors keep. The doors that judge a
# finished call — the judges, the replay — ask this and not `owner`, which knows the org alone:
# a sandbox person's key replayed the org's production calls until 2026-09-26.
NO_SUCH_CALL = "no call {call} in this key's org and world"


async def the_calls_corner(index: CallIndex, key: KeyRecord, call: str) -> CallCorner:
    """The corner this call was opened in, when it is the key's own; one 404 sentence otherwise."""
    whose = corner_of(key)
    corner = await index.corner_of_call(call)
    if corner is None or not corner.is_in(whose.org, whose.env, whose.holder or ""):
        raise HTTPException(status_code=404, detail=NO_SUCH_CALL.format(call=call))
    return corner


async def the_reader(
    connection: HTTPConnection,
    keys: KeysDep,
    settings: SettingsDep,
    members: MembersDep,
    token: Annotated[str | None, Query()] = None,
) -> Reader:
    """The reader, or 401. An unknown key is told nothing about why it is unknown."""
    reader = await reading(connection, keys, settings, token)
    if reader is None:
        raise HTTPException(401, "a log is read with a key", {"WWW-Authenticate": "Bearer"})
    # A key reads in the world it names, in its own corner or the colleague's an admin named.
    if reader.key is not None:
        try:
            looking = await as_asked(reader.key, connection.headers, members, settings)
        except PermissionError as refused:
            raise HTTPException(403, str(refused)) from refused
        if looking is not reader.key:
            reader = replace(reader, key=looking)
    # A token's grant already says what it reads; a key reads a call with the `calls` scope.
    if reader.key is not None and (closed := not_opening(reader.key, READS)) is not None:
        raise HTTPException(403, closed)
    return reader


# The scope every read door asks of a key. Named here because the_reader is the one door they
# share, and the test over the routes reads it off this function the way it reads a scoped dep —
# under the same attribute name and in the same shape `opening()` writes, spelled there once.
READS = "calls"
the_reader.__dict__[SCOPE_OF_THE_DOOR] = frozenset({READS})


# A process with neither a LiveKit pair nor a dev key can still serve API keys; it just cannot
# verify a participate token, and says so by having none rather than by raising at the door.
def _a_secret(settings: Settings) -> LivekitKeys | None:
    """The LiveKit pair this process has, or None when it has nothing to verify with."""
    try:
        return secret_for(settings)
    except RuntimeError:
        return None


# The projection is decided from what the reader IS, and applied at the sink. It needs the agent's
# declaration to know which state fields may leave, and the registry is where a declaration lives.
def a_projection(registry: RegistryDep) -> Project:
    """What this reader may see of an entry, by the contract's two projections and nothing else."""

    def project(entry: Entry, reader: Reader) -> JsonObject | None:
        return project_entry(
            encode(entry), reader.projection, declared_by(registry, entry.agent), reader.viewer
        )

    return project


# An entry names its agent and not the world it was written in, and this reader may hold no key
# at all: the registry answers with the declaration a stranger's log was written under.
def declared_by(registry: Registry, agent: str) -> AgentConfig | None:
    """What the agent said about its state fields. An agent nobody holds declared nothing."""
    return registry.declared(agent)


# ── what the reader asked for ───────────────────────────────────────────────────


def the_cursor(
    after: Annotated[int, Query(ge=0)] = 0,
    last_event_id: Annotated[str | None, Header()] = None,
) -> int:
    """Where to read from. A reconnecting EventSource sends Last-Event-ID; the higher one wins."""
    return max(after, _a_seq(last_event_id))


def the_filter(
    types: Annotated[str | None, Query()] = None,
    durable: Annotated[bool, Query()] = False,
) -> Filter:
    """`types=a.b,c.d` and `durable=1`, refused by name when they are not what a filter may be."""
    return Filter.of(types, durable)


def wants_sse(accept: str | None) -> bool:
    """Whether this reader asked for the stream. One URL, two flavours, and Accept decides."""
    return SSE in (accept or "")


CursorDep = Annotated[int, Depends(the_cursor)]
FilterDep = Annotated[Filter, Depends(the_filter)]
ProjectDep = Annotated[Project, Depends(a_projection)]
ReaderDep = Annotated[Reader, Depends(the_reader)]
LimitDep = Annotated[int, Query(ge=1, le=DEFAULT_LIMIT)]
AcceptDep = Annotated[str | None, Header()]


# ── the two flavours ────────────────────────────────────────────────────────────


# The protocol's LogPage, after the projection: an entry is whatever the reader's projection let
# leave of it, and the public one keeps four fields of the envelope, so the entries are open JSON
# objects here and the protocol's Entry only for a tenant (docs/protocol/projections.md).
class ProjectedPage(WireModel):
    """One page of a log as this reader may see it: the entries, whether it is open, and next."""

    entries: list[JsonObject]
    live: bool
    next: int | None


# next is the last seq the page READ, not the last it kept. With a filter the two differ, and the
# read one is the only one that makes progress: a page whose every entry was filtered out still
# moves the cursor past them, where next=null would tell the reader it had reached the end. An
# empty read is the end — null is then the honest answer to "where do I resume".
def page(
    read: list[Entry], kept: list[Entry], live: bool, project: Project, reader: Reader
) -> ProjectedPage:
    """One JSON page: the entries this reader may see, whether the log is still open, and next."""
    said = (project(entry, reader) for entry in kept)
    return ProjectedPage(
        # An entry the projection drops is absent, not null: it never leaves at all. `next` still
        # counts it, so the cursor moves past what this reader was never going to be shown.
        entries=[one for one in said if one is not None],
        live=live,
        next=read[-1].seq if read else None,
    )


def sse(
    entries: AsyncIterator[Entry],
    project: Project,
    reader: Reader,
    *,
    ends_at: str | None = TERMINAL_EVENT,
) -> StreamingResponse:
    """The same entries as a stream that stays open, and ends where its log does — if it ends."""
    return a_stream(_projected(entries, project, reader, ends_at))


# Any SSE door of this gateway, whatever it says per entry: a door that projects for a tenant and
# the operator's, which wraps each entry with its org, write the same frames on the same clock.
def a_stream(said: AsyncIterator[tuple[Entry, JsonObject]]) -> StreamingResponse:
    """Each entry and what it says, as SSE: retry first, a frame each, a ping when it is quiet."""
    return StreamingResponse(_frames(said), media_type=SSE, headers=SSE_HEADERS)


async def _frames(said: AsyncIterator[tuple[Entry, JsonObject]]) -> AsyncIterator[str]:
    """retry first, then a frame per entry and a ping whenever nothing came for a while."""
    yield f"retry: {RETRY_MS}\n\n"
    async for one in paced(said, PING_SECONDS):
        yield PING if one is None else _frame(*one)


async def _projected(
    entries: AsyncIterator[Entry], project: Project, reader: Reader, ends_at: str | None
) -> AsyncIterator[tuple[Entry, JsonObject]]:
    """What this reader may see of each entry, stopping at the last event even when it was not
    theirs to see: the log is over either way."""
    async for entry in entries:
        said = project(entry, reader)
        if said is not None:
            yield entry, said
        if entry.type == ends_at:
            return


# id is the seq, so a reconnect resumes exactly where this left off — the markers ride the stream
# at the seq of the last entry they speak for, which makes them safe to resume from too.
def _frame(entry: Entry, said: JsonObject) -> str:
    """One entry as SSE: its seq, its type, and what this reader may see as the data line."""
    return an_sse_frame(entry.type, said, id=entry.seq)


# The one place a frame is spelled (the log's, the usage rows', the app's commands'): the id when
# the reader can resume from it, the event, the data on one line, the blank line that ends it.
def an_sse_frame(event: str, data: JsonObject, *, id: int | None = None) -> str:
    """One SSE frame, compact JSON as the data line."""
    said = json.dumps(data, separators=(",", ":"))
    head = "" if id is None else f"id: {id}\n"
    return f"{head}event: {event}\ndata: {said}\n\n"


# The stream and the clock are two sources and SSE needs both. One pending task carried across the
# timeout is the whole trick: re-awaiting a fresh __anext__ after a ping would drop the entry the
# first one is still waiting for.
async def paced[T](coming: AsyncIterator[T], every: float) -> AsyncIterator[T | None]:
    """Every item as it comes, and None whenever `every` seconds pass with nothing to send."""
    pending: asyncio.Task[T] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(anext(coming))
            done, _ = await asyncio.wait({pending}, timeout=every)
            if not done:
                yield None
                continue
            finished, pending = pending, None
            try:
                yield finished.result()
            except StopAsyncIteration:
                return
    finally:
        if pending is not None:
            pending.cancel()


# ── whether the log is over ─────────────────────────────────────────────────────


# The Store keeps no flag to ask, on purpose: what ends a call is the protocol's terminal event,
# and the store must not have to read the protocol to write a row. So the tail is the answer.
async def ended(store: Store, call: str) -> bool:
    """Whether this call's log is sealed: its last entry is the terminal one, or it is not over."""
    latest = await store.latest_seq(call)
    if latest == 0:
        return False
    tail = await store.since(call, after=latest - 1, limit=1)
    return bool(tail) and tail[-1].type == TERMINAL_EVENT


def _a_seq(header: str | None) -> int:
    """A Last-Event-ID as the seq it is. Anything else is a browser's business, not an error."""
    try:
        return max(int(header or 0), 0)
    except ValueError:
        return 0

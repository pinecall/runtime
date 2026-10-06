"""An agent's WhatsApp inbox by contact: the threads, one thread, read, written to."""

import time
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from pinecall.channels.whatsapp import WINDOW_S
from pinecall.domain.errors import (
    Conflict,
    NotFound,
)
from pinecall.domain.names import THE_WIDGET, parse_channel
from pinecall.domain.scope import Scope
from pinecall.gateway import _deps
from pinecall.gateway._deps import Acting, CallsKey, GatewayDep, ScopeDep, TalkKey
from pinecall.gateway._gateway import Gateway
from pinecall.log import facts, inbox, queries
from pinecall.tenancy import keys
from pinecall.wire.commands import SayVerb, SupervisorVerb
from pinecall.wire.frames import Entry
from pinecall.wire.parts import Supervisor, ThreadKind
from pinecall.wire.rest.calls import (
    ThreadLast,
    ThreadList,
    ThreadMessage,
    ThreadMessageRequest,
    ThreadMessageResponse,
    ThreadResponse,
    ThreadRow,
)

router = APIRouter()


THREADS_A_PAGE = 30

# The calls merged into one thread, newest first: each costs a log read.
CALLS_IN_A_THREAD = 20

NO_THREAD = "no thread with {contact} on agent {agent} in this corner"

ONLY_WHATSAPP = (
    "a message is written on a WhatsApp thread, and {contact}'s newest call is {channel}"
)

WINDOW_CLOSED = (
    "WhatsApp's window with {contact} closed 24 h after their last message: only a template "
    "reaches them now"
)

NOTHING_OPEN = (
    "{contact}'s conversation went quiet and is sealed: their next message opens a new one"
)


class ThreadQuery(BaseModel):
    """One page of an inbox: after the cursor of the last one, so many contacts."""

    after: str | None = None
    limit: int = Field(THREADS_A_PAGE, ge=1, le=_deps.LONGEST_LIST)


@router.get("/v1/agents/{slug}/threads")
async def list_threads(
    slug: str,
    key: CallsKey,
    where: ScopeDep,
    gateway: GatewayDep,
    page: Annotated[ThreadQuery, Query()],
) -> ThreadList:
    """The agent's contacts, the one that moved last first, with what this reader has not read."""
    wanted = inbox.Inbox(where, slug, _reader_of(key))
    found = await inbox.threads(
        gateway.connections.pool, wanted, after=page.after, limit=page.limit
    )
    return ThreadList(threads=[_line_of(row) for row in found.rows], next=found.next)


@router.get("/v1/agents/{slug}/threads/{contact}")
async def get_thread(
    slug: str, contact: str, _key: CallsKey, where: ScopeDep, gateway: GatewayDep
) -> ThreadResponse:
    """A contact's calls with the agent merged into one thread, oldest first."""
    calls = await _thread_of(gateway, where, slug, contact)
    facts = await queries.facts_of_calls(gateway.connections.pool, calls)
    messages: list[ThreadMessage] = []
    for call in reversed(calls):
        if call in facts:
            messages += _said_on(facts[call], await gateway.logs.store.whole(call))
    name = next((facts[call].name for call in calls if call in facts and facts[call].name), None)
    return ThreadResponse(contact=contact, name=name, messages=messages)


# The read cursor is the person's; a server's key keeps its own.
@router.post("/v1/agents/{slug}/threads/{contact}/read", status_code=204)
async def mark_thread_read(
    slug: str, contact: str, key: CallsKey, where: ScopeDep, gateway: GatewayDep
) -> None:
    """This reader has read the thread up to now."""
    await _thread_of(gateway, where, slug, contact)
    await inbox.read(
        gateway.connections.pool, inbox.Inbox(where, slug, _reader_of(key)), contact, time.time()
    )


# A supervisor's `say` on the open conversation, sent to the contact by its reply tap. Meta lets
# a business write freely only within 24 h of the contact's last message.
@router.post("/v1/agents/{slug}/threads/{contact}/messages", status_code=202)
async def send_thread_message(
    slug: str, contact: str, body: ThreadMessageRequest, key: TalkKey, gateway: GatewayDep
) -> ThreadMessageResponse:
    """Say something as the agent on the contact's open WhatsApp conversation."""
    where = keys.scope_of(key.bearer, key.env)
    newest = (await _thread_of(gateway, where, slug, contact))[0]
    facts = (await queries.facts_of_calls(gateway.connections.pool, [newest])).get(newest)
    channel = None if facts is None else facts.channel
    if facts is None or channel != "whatsapp":
        raise Conflict(ONLY_WHATSAPP.format(contact=contact, channel=channel))
    # The log's own clock, which stamped when the contact last wrote.
    if gateway.logs.store.clock() - max(facts.heard_at, default=0.0) > WINDOW_S:
        raise Conflict(WINDOW_CLOSED.format(contact=contact))
    served = gateway.live.calls.get(newest)
    if served is None or served.session is None:
        raise Conflict(NOTHING_OPEN.format(contact=contact))
    member = key.bearer.member
    by = Supervisor(id=_reader_of(key), name=None if member is None else member.name)
    await served.session.supervise(SupervisorVerb(by=by, verb=SayVerb(text=body.text)))
    return ThreadMessageResponse(contact=contact, call=newest)


async def _thread_of(gateway: Gateway, where: Scope, slug: str, contact: str) -> list[str]:
    calls = await inbox.calls_with(
        gateway.connections.pool, where, slug, contact, limit=CALLS_IN_A_THREAD
    )
    if not calls:
        raise NotFound(NO_THREAD.format(contact=contact, agent=slug))
    return calls


def _reader_of(key: Acting) -> str:
    return key.bearer.key.subject or key.bearer.key.key_id


def _line_of(row: inbox.InboxRow) -> ThreadRow:
    newest = row.newest
    kind: ThreadKind = "call" if newest.spoken else ("in" if newest.last_in else "out")
    data = newest.outcome if newest.spoken else newest.last_text
    return ThreadRow(
        contact=row.contact,
        name=row.name,
        channel_last=parse_channel(newest.channel or THE_WIDGET),
        last=ThreadLast(text=data, at=row.moved_at, kind=kind),
        unread=row.unread,
        calls=row.calls,
    )


# A written call is its turns; a spoken call is one pill with its length and whether it came up.
def _said_on(call_facts: facts.CallFacts, entries: list[Entry]) -> list[ThreadMessage]:
    channel = parse_channel(call_facts.channel or THE_WIDGET)
    if not call_facts.spoken:
        return [
            ThreadMessage(
                kind="in" if entry.type == "turn.user" else "out",
                text=str(entry.data.get("text") or ""),
                at=entry.ts,
                call=call_facts.call,
                channel=channel,
            )
            for entry in entries
            if entry.type in {"turn.user", "turn.agent"}
        ]
    ended = next((entry for entry in entries if entry.type == "call.ended"), None)
    lasted = None if ended is None else ended.data.get("duration_s")
    return [
        ThreadMessage(
            kind="call",
            text=call_facts.outcome,
            at=entries[0].ts if entries else 0.0,
            call=call_facts.call,
            channel=channel,
            duration_s=float(lasted) if isinstance(lasted, int | float) else None,
            answered=any(entry.type == "call.started" for entry in entries),
        )
    ]

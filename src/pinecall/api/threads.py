"""The inbox: an agent's calls by contact, read per person, and a message said on WhatsApp."""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from pinecall.api._deps import CallIndexDep, CallsKeyDep, SnapshotsDep, StoreDep, TalkKeyDep
from pinecall.api.supervise.aiming import QueueingDep, VerbRefused, aimed
from pinecall.api.whatsapp.threads import WINDOW_SECONDS
from pinecall.auth.corner import Corner, corner_of
from pinecall.auth.keys import KeyRecord
from pinecall.auth.scopes import KEY_PROJECTION, Reader
from pinecall.log.entry import Entry
from pinecall.log.facts import CallFacts
from pinecall.log.replay import whole
from pinecall.log.store.index import ThreadRow
from pinecall_protocol import verbs
from pinecall_protocol.rest import (
    Thread,
    ThreadLine,
    ThreadList,
    ThreadMessage,
    ThreadSaid,
    ThreadSay,
)

router = APIRouter()

A_SCREENFUL = 30

# How many of a contact's calls one thread is read from, newest first: a year of a regular's calls
# is not one screen, and the log of each is a read.
CALLS_IN_A_THREAD = 20

NO_THREAD = "no thread with {contact} on agent {agent} in this key's org and world"

# What a message on a thread may and may not reach: the provider's rule, and this runtime's.
ONLY_WHATSAPP = "a message is said on a WhatsApp thread, and {contact}'s newest call is {channel}"
WINDOW_CLOSED = (
    "WhatsApp's customer-service window with {contact} closed {hours:.0f} h after their last "
    "message: the business may only send them a template now"
)
NOTHING_OPEN = (
    "{contact}'s last conversation idled out and is sealed: a message is said on an open one, "
    "which their next message opens"
)

WRITTEN = 202


@router.get("/v1/agents/{slug}/threads")
async def threads(
    slug: str,
    key: CallsKeyDep,
    index: CallIndexDep,
    after: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = A_SCREENFUL,
) -> ThreadList:
    """The agent's contacts, the thread that moved last first, and what this reader has not read."""
    corner = corner_of(key)
    page = await index.threads(
        corner.org, corner.env, corner.holder or "", slug, _the_reader(key), after, limit
    )
    return ThreadList(threads=[_a_line(row) for row in page.rows], next=page.next)


@router.get("/v1/agents/{slug}/threads/{contact}")
async def thread(
    slug: str, contact: str, key: CallsKeyDep, index: CallIndexDep, store: StoreDep
) -> Thread:
    """Every call of one contact with the agent, merged into one thread, oldest first."""
    corner = corner_of(key)
    calls = await _calls_of(index, corner, slug, contact)
    facts = await index.facts_of(calls)
    messages: list[ThreadMessage] = []
    for call in reversed(calls):
        messages += _said_on(facts[call], await whole(store, call))
    name = next((facts[call].name for call in calls if facts[call].name), None)
    return Thread(contact=contact, name=name, messages=messages)


# The read cursor is a PERSON's: two people of one team read one inbox, and neither has read it for
# the other. A machine key names nobody and reads as itself.
@router.post("/v1/agents/{slug}/threads/{contact}/read", status_code=204)
async def read(slug: str, contact: str, key: CallsKeyDep, index: CallIndexDep) -> Response:
    """This reader has read the contact's thread up to now."""
    corner = corner_of(key)
    await _calls_of(index, corner, slug, contact)
    await index.read(
        corner.org, corner.env, corner.holder or "", slug, _the_reader(key), contact, time.time()
    )
    return Response(status_code=204)


# Said as the agent, through the very path a supervisor's `say` takes: the words land on the open
# conversation's log as supervisor.said and turn.agent, and the thread's watcher sends turn.agent
# to the contact. WhatsApp lets a business write freely only within 24 h of the contact's last
# message; past that, and on a conversation that already idled out, the door says which in a 409.
@router.post("/v1/agents/{slug}/threads/{contact}/messages", status_code=WRITTEN)
async def say(
    slug: str,
    contact: str,
    said: ThreadSay,
    key: TalkKeyDep,
    index: CallIndexDep,
    live: QueueingDep,
    store: StoreDep,
    snapshots: SnapshotsDep,
) -> ThreadSaid:
    """The words said to the contact on their open WhatsApp conversation."""
    corner = corner_of(key)
    newest = (await _calls_of(index, corner, slug, contact))[0]
    facts = (await index.facts_of([newest]))[newest]
    if facts.channel != "whatsapp":
        raise HTTPException(409, ONLY_WHATSAPP.format(contact=contact, channel=facts.channel))
    last_heard = max(facts.heard_at, default=0.0)
    if time.time() - last_heard > WINDOW_SECONDS:
        raise HTTPException(409, WINDOW_CLOSED.format(contact=contact, hours=WINDOW_SECONDS / 3600))
    if live.of(newest) is None:
        raise HTTPException(409, NOTHING_OPEN.format(contact=contact))
    reader = Reader(projection=KEY_PROJECTION, key=key, subject=key.subject, name=key.name)
    try:
        await aimed(live, store, snapshots, reader, newest, verbs.SayVerb(text=said.text))
    except VerbRefused as refused:
        raise HTTPException(refused.status, refused.detail) from refused
    return ThreadSaid(contact=contact, call=newest)


async def _calls_of(index: CallIndexDep, corner: Corner, agent: str, contact: str) -> list[str]:
    """The contact's newest calls with the agent in this corner, or 404 when there are none."""
    calls = await index.calls_with(
        corner.org, corner.env, corner.holder or "", agent, contact, CALLS_IN_A_THREAD
    )
    if not calls:
        raise HTTPException(404, NO_THREAD.format(contact=contact, agent=agent))
    return calls


def _the_reader(key: KeyRecord) -> str:
    """Whose read cursor: the person the key was minted for, or the key itself."""
    return key.subject or key.key_id


def _a_line(row: ThreadRow) -> ThreadLine:
    """One contact as the inbox lists them."""
    newest = row.newest
    return ThreadLine.model_validate(
        {
            "contact": row.contact,
            "name": row.name,
            "channel_last": newest.channel or "web",
            "last": {
                "text": newest.outcome if newest.spoken else newest.last_text,
                "at": row.moved_at,
                "kind": "call" if newest.spoken else ("in" if newest.last_in else "out"),
            },
            "unread": row.unread,
            "calls": row.calls,
        }
    )


# A written call is its turns, each a message; a spoken one is one pill — when it came up, how long
# it lasted, whether it was answered at all, and its outcome — because a transcript of a phone call
# is the call's own screen, not an inbox's.
def _said_on(facts: CallFacts, entries: Sequence[Entry]) -> list[ThreadMessage]:
    """The messages one call adds to its contact's thread."""
    channel = facts.channel or "web"
    if facts.spoken:
        started = any(entry.type == "call.started" for entry in entries)
        ended = next((entry for entry in entries if entry.type == "call.ended"), None)
        duration = None if ended is None else ended.data.get("duration_s")
        pill = {
            "kind": "call",
            "text": facts.outcome,
            "at": entries[0].ts if entries else 0.0,
            "call": facts.call,
            "channel": channel,
            "duration_s": duration if isinstance(duration, int | float) else None,
            "answered": started,
        }
        return [ThreadMessage.model_validate(pill)]
    return [
        ThreadMessage.model_validate(
            {
                "kind": "in" if entry.type == "turn.user" else "out",
                "text": str(entry.data.get("text") or ""),
                "at": entry.ts,
                "call": facts.call,
                "channel": channel,
            }
        )
        for entry in entries
        if entry.type in ("turn.user", "turn.agent")
    ]

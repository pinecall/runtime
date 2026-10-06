"""Tests for a contact's threads: the inbox, what is unread, the calls with a contact."""

from pinecall.domain.scope import Scope
from pinecall.log.inbox import Inbox, calls_with, read, threads
from pinecall.log.store import Store
from tests.conftest import postgres
from tests.log.conftest import AGENT, ACall, logged_call


@postgres
async def test_an_inbox_is_a_line_per_contact_and_counts_what_the_reader_has_not_read(
    store: Store, org: str
) -> None:
    await logged_call(store, org, ACall(channel="whatsapp", caller="+34611", ended=False))
    spoken = await logged_call(store, org, ACall(caller="+34622"))
    written = await logged_call(store, org, ACall(channel="whatsapp", caller="+34611"))
    mine = Inbox(Scope(org), AGENT, "m_1")
    inbox = await threads(store.pool, mine, after=None, limit=10)
    assert [(row.contact, row.calls, row.unread) for row in inbox.rows] == [
        ("+34611", 2, 2),
        ("+34622", 1, 1),
    ]
    assert inbox.rows[0].newest.call == written
    first = await threads(store.pool, mine, after=None, limit=1)
    assert [row.contact for row in first.rows] == ["+34611"]
    after = await threads(store.pool, mine, after=first.next, limit=1)
    assert ([row.contact for row in after.rows], after.next) == (["+34622"], None)
    await read(store.pool, mine, "+34611", 10_000.0)
    assert [row.unread for row in (await threads(store.pool, mine, after=None, limit=10)).rows] == [
        0,
        1,
    ]
    theirs = Inbox(Scope(org), AGENT, "m_2")
    assert [
        row.unread for row in (await threads(store.pool, theirs, after=None, limit=10)).rows
    ] == [2, 1]
    assert await calls_with(store.pool, Scope(org), AGENT, "+34622", limit=5) == [spoken]

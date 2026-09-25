"""The inbox: an agent's calls by contact, what each person has read, and where a message may go."""

from __future__ import annotations

import time
from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.api.threads import NO_THREAD, NOTHING_OPEN, ONLY_WHATSAPP, WINDOW_CLOSED
from pinecall.api.whatsapp.threads import WINDOW_SECONDS
from pinecall.auth.keys import NOT_OPENED, KeyRecord, MemoryKeys
from pinecall.auth.members_memory import MemoryMembers
from pinecall.auth.world import ENV_HEADER
from pinecall.log.store import MemoryStore
from pinecall.types import PRODUCTION, Member
from tests.api.conftest import A_KEY, A_RECORD, AGENT
from tests.api.talking import got

pytestmark = pytest.mark.unit

INBOX = f"/v1/agents/{AGENT}/threads"
ANA = "+34600000001"
LUIS = "+34611000000"
A_QA_KEY = "pk_test_reads"
A_QA = KeyRecord(key_id="k_qa", org=A_RECORD.org, scopes=frozenset({"calls"}), subject="m_qa")
# The person that key is, and the org lets them read production, where the conversations are.
QA = Member(
    id="m_qa",
    org=A_RECORD.org,
    email="qa@x.test",
    name="QA",
    role="qa",
    status="active",
    production=True,
)
THE_SHOPS_KEY = "pk_test_the_shop"
THE_SHOP = KeyRecord(key_id="k_shop", org="tienda")


class Clock:
    """A clock that ticks a second per entry, from a moment a test may move."""

    def __init__(self) -> None:
        self.now = time.time() - 600

    def __call__(self) -> float:
        self.now += 1.0
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock: Clock) -> MemoryStore:
    return MemoryStore(clock=clock)


@pytest.fixture
def members() -> MemoryMembers:
    return MemoryMembers([QA])


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, A_QA_KEY: A_QA, THE_SHOPS_KEY: THE_SHOP})


async def a_conversation(
    store: MemoryStore, call: str, contact: str, *, channel: str = "whatsapp", said: int = 1
) -> None:
    """One call from the contact: written turns on WhatsApp, a spoken call on the phone."""
    await store.owned(call, AGENT, A_RECORD.org, "production", "")
    line = {"channel": channel, "from": contact, "to": "+34910000000", "caller": None}
    await store.append(call, AGENT, "call.started", {**line, "direction": "inbound"})
    for n in range(said):
        await store.append(call, AGENT, "turn.user", {"text": f"mensaje {n}"})
        await store.append(call, AGENT, "turn.agent", {"text": f"respuesta {n}"})
    if channel == "phone":
        ended = {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 1, "duration_s": 42}
        await store.append(call, AGENT, "call.ended", ended)
        await store.append(call, AGENT, "call.summary", {"outcome": "pidió cita"})


def post(gateway: TestClient, path: str, body: Any = None, bearer: str = A_KEY) -> Any:
    """A write from the console at production, which says the world it believes it is in."""
    handle: Any = gateway
    headers = {"Authorization": f"Bearer {bearer}", ENV_HEADER: PRODUCTION}
    return handle.post(path, json=body, headers=headers)


async def test_the_inbox_is_a_line_per_contact_and_each_person_reads_their_own(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_conversation(store, "CA_luis", LUIS, channel="phone")
    await a_conversation(store, "CA_ana_1", ANA, said=2)
    await a_conversation(store, "CA_ana_2", ANA)
    _, inbox = got(gateway, INBOX)
    assert [(one["contact"], one["calls"], one["unread"]) for one in inbox["threads"]] == [
        (ANA, 2, 3),
        (LUIS, 1, 1),
    ]
    assert inbox["threads"][0]["last"]["kind"] == "out"
    assert inbox["threads"][1]["last"] == {
        "text": "pidió cita",
        "at": inbox["threads"][1]["last"]["at"],
        "kind": "call",
    }
    assert post(gateway, f"{INBOX}/{ANA}/read").status_code == 204
    assert [one["unread"] for one in got(gateway, INBOX)[1]["threads"]] == [0, 1]
    assert [one["unread"] for one in got(gateway, INBOX, A_QA_KEY, PRODUCTION)[1]["threads"]] == [
        3,
        1,
    ]
    _, first = got(gateway, f"{INBOX}?limit=1")
    _, rest = got(gateway, f"{INBOX}?limit=1&after={first['next']}")
    assert ([one["contact"] for one in rest["threads"]], rest["next"]) == ([LUIS], None)


async def test_a_thread_merges_a_contacts_calls_oldest_first_and_a_spoken_one_is_a_pill(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_conversation(store, "CA_ana_call", ANA, channel="phone")
    await a_conversation(store, "CA_ana_chat", ANA)
    status, thread = got(gateway, f"{INBOX}/{ANA}")
    assert status == 200
    kinds = [(one["kind"], one["text"], one["call"]) for one in thread["messages"]]
    assert kinds == [
        ("call", "pidió cita", "CA_ana_call"),
        ("in", "mensaje 0", "CA_ana_chat"),
        ("out", "respuesta 0", "CA_ana_chat"),
    ]
    assert (thread["messages"][0]["duration_s"], thread["messages"][0]["answered"]) == (42, True)


async def test_another_orgs_key_finds_no_thread_and_an_empty_inbox(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_conversation(store, "CA_ana", ANA)
    assert got(gateway, INBOX, THE_SHOPS_KEY)[1] == {"threads": [], "next": None}
    status, body = got(gateway, f"{INBOX}/{ANA}", THE_SHOPS_KEY)
    assert (status, body["detail"]) == (404, NO_THREAD.format(contact=ANA, agent=AGENT))


async def test_a_message_goes_only_where_whatsapp_and_an_open_conversation_allow(
    gateway: TestClient, store: MemoryStore, clock: Clock
) -> None:
    await a_conversation(store, "CA_luis", LUIS, channel="phone")
    said = post(gateway, f"{INBOX}/{LUIS}/messages", {"text": "hola"})
    assert (said.status_code, said.json()["detail"]) == (
        409,
        ONLY_WHATSAPP.format(contact=LUIS, channel="phone"),
    )
    clock.now = time.time() - WINDOW_SECONDS - 3600
    await a_conversation(store, "CA_old", "+34622")
    closed = post(gateway, f"{INBOX}/+34622/messages", {"text": "hola"})
    hours = WINDOW_SECONDS / 3600
    assert closed.json()["detail"] == WINDOW_CLOSED.format(contact="+34622", hours=hours)
    clock.now = time.time() - 60
    await a_conversation(store, "CA_ana", ANA)
    sealed = post(gateway, f"{INBOX}/{ANA}/messages", {"text": "hola"})
    assert (sealed.status_code, sealed.json()["detail"]) == (409, NOTHING_OPEN.format(contact=ANA))


def test_saying_takes_talk(gateway: TestClient) -> None:
    refused = post(gateway, f"{INBOX}/{ANA}/messages", {"text": "hola"}, A_QA_KEY)
    assert refused.json()["detail"] == NOT_OPENED.format(scope="talk", opens="calls")

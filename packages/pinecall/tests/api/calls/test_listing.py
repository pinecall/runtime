"""GET /v1/agents/{slug}/sessions: the calls of one agent, newest first, one row each."""

import pytest
from starlette.testclient import TestClient

from pinecall.log.store import MemoryStore
from pinecall.tokens.scopes import mint_room_token
from tests.api.conftest import A_LIVEKIT, A_RECORD
from tests.api.talking import got

pytestmark = pytest.mark.unit

THE_AGENT = "clinica-norte"

# The three entries a call's own log opens and closes with, as the worker writes them.
ARRIVED = {"channel": "web", "from": "web_1", "to": THE_AGENT, "caller": None}
RINGING = {**ARRIVED, "route": {"channel": "web", "number": None}}
UP = {**ARRIVED, "direction": "inbound", "started_at": 1.0}
OVER = {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 9.0, "duration_s": 8.0}


def sessions_of(agent: str = THE_AGENT) -> str:
    """The door, by the agent whose calls are asked for."""
    return f"/v1/agents/{agent}/sessions"


async def a_call(store: MemoryStore, call: str, *, ended: bool = False) -> None:
    await store.owned(call, THE_AGENT, A_RECORD.org)
    """One call in the log as a worker writes it: how it arrived, and how it went if it is over."""
    await store.append(call, THE_AGENT, "call.ringing", dict(RINGING))
    await store.append(call, THE_AGENT, "call.started", dict(UP))
    if ended:
        await store.append(call, THE_AGENT, "call.ended", dict(OVER))


async def test_the_agents_calls_are_listed_newest_first(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call(store, "CA_first")
    await a_call(store, "CA_second")
    _status, body = got(gateway, sessions_of())
    assert [line["call"] for line in body["calls"]] == ["CA_second", "CA_first"]


async def test_a_line_says_how_the_call_arrived_and_whether_it_is_still_going(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call(store, "CA_up")
    await a_call(store, "CA_over", ended=True)
    over, up = got(gateway, sessions_of())[1]["calls"]
    assert (up["call"], up["status"], up["channel"], up["live"]) == ("CA_up", "active", "web", True)
    assert (over["call"], over["status"], over["live"]) == ("CA_over", "ended", False)


async def test_another_agents_calls_are_not_this_agents(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call(store, "CA_first")
    assert got(gateway, sessions_of("tienda-sur")) == (200, {"calls": [], "total": 0, "next": None})


async def test_the_limit_cuts_the_list_at_the_newest(
    gateway: TestClient, store: MemoryStore
) -> None:
    for call in ("CA_1", "CA_2", "CA_3"):
        await a_call(store, call)
    _status, body = got(gateway, f"{sessions_of()}?limit=2")
    assert [line["call"] for line in body["calls"]] == ["CA_3", "CA_2"]


# The agent's history names every caller it ever answered, so a token minted for one call is the
# one reader this door refuses outright — the same rule the agent's own log is read under.
async def test_a_token_bound_to_one_call_may_not_read_the_agents_list(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call(store, "CA_first")
    token = mint_room_token("CA_first", "participate", 9999999999, A_LIVEKIT)
    assert got(gateway, f"{sessions_of()}?token={token}", bearer=None)[0] == 403


def test_the_list_takes_a_key(gateway: TestClient) -> None:
    assert got(gateway, sessions_of(), bearer=None)[0] == 401

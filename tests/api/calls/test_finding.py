"""The two list doors filtered, counted and paged, each row with its verdict and its flags."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from pinecall.log.store import MemoryStore
from pinecall.types.json import JsonObject
from tests.api.calls.test_listing import OVER, RINGING, THE_AGENT, UP
from tests.api.conftest import A_RECORD
from tests.api.talking import got

pytestmark = pytest.mark.unit

ANOTHER_AGENT = "tienda-sur"


async def a_call(
    store: MemoryStore,
    call: str,
    *,
    agent: str = THE_AGENT,
    org: str = A_RECORD.org,
    caller: str = "web_1",
    channel: str = "web",
    outcome: str = "nothing to say",
    judges: list[JsonObject] | None = None,
    took_over: bool = False,
    env: str = "production",
) -> None:
    """One finished call as a worker writes it, with its summary and its verdict."""
    await store.owned(call, agent, org, env, "")
    line = {"channel": channel, "from": caller, "route": {"channel": channel, "number": None}}
    await store.append(call, agent, "call.ringing", {**RINGING, **line})
    await store.append(call, agent, "call.started", {**UP, "channel": channel, "from": caller})
    if took_over:
        await store.append(call, agent, "supervisor.took_over", {"by": {"id": "m_sup"}})
    await store.append(call, agent, "call.ended", dict(OVER))
    summary = {"reason": "caller_hung_up", "outcome": outcome, "duration_s": 8.0, "turns": 1}
    await store.append(call, agent, "call.summary", summary)
    await store.append(call, agent, "call.score", {"judges": judges or [], "judge_calls": 0})


def judged(name: str, verdict: str, reason: str) -> JsonObject:
    """One judgment as call.score carries it."""
    return {"name": name, "verdict": verdict, "criteria": "", "reason": reason, "evidence": {}}


async def test_a_row_carries_how_the_judges_answered_and_what_to_look_at_first(
    gateway: TestClient, store: MemoryStore
) -> None:
    promised = judged("promises", "broken", "promised a call back and booked none")
    await a_call(store, "CA_promised", judges=[judged("consent", "held", ""), promised])
    await a_call(store, "CA_taken", took_over=True)
    _, body = got(gateway, "/v1/sessions")
    rows = {line["call"]: line for line in body["calls"]}
    assert rows["CA_promised"]["score"] == {
        "held": 1,
        "judged": 2,
        "passed": False,
        "reason": "promised a call back and booked none",
    }
    assert rows["CA_promised"]["flags"] == ["low_score", "promise"]
    assert (rows["CA_taken"]["score"], rows["CA_taken"]["flags"]) == (None, ["escalated"])


async def test_the_orgs_list_is_found_by_words_agent_and_door(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call(store, "CA_1", caller="+34 600 123 456", channel="phone")
    await a_call(store, "CA_2", agent=ANOTHER_AGENT, outcome="Booked the Tuesday visit")
    await a_call(store, "CA_3", channel="whatsapp", caller="+34 611 000 000")
    await a_call(store, "CA_theirs", org="vecina", outcome="booked")

    def calls(query: str) -> tuple[list[str], int]:
        _, body = got(gateway, f"/v1/sessions?{query}")
        return [line["call"] for line in body["calls"]], body["total"]

    assert calls("q=600123") == (["CA_1"], 1)
    assert calls("q=booked") == (["CA_2"], 1)
    assert calls(f"agent={ANOTHER_AGENT}") == (["CA_2"], 1)
    assert calls("channel=whatsapp") == (["CA_3"], 1)
    assert calls("q=ca_") == (["CA_3", "CA_2", "CA_1"], 3)
    assert got(gateway, "/v1/sessions?channel=fax")[0] == 422


async def test_a_list_is_paged_below_a_cursor_and_says_when_it_is_the_last_page(
    gateway: TestClient, store: MemoryStore
) -> None:
    for call in ("CA_1", "CA_2", "CA_3"):
        await a_call(store, call)
    _, first = got(gateway, f"/v1/agents/{THE_AGENT}/sessions?limit=2")
    assert ([line["call"] for line in first["calls"]], first["total"], first["next"]) == (
        ["CA_3", "CA_2"],
        3,
        "CA_2",
    )
    _, last = got(gateway, f"/v1/agents/{THE_AGENT}/sessions?limit=2&before=CA_2")
    assert ([line["call"] for line in last["calls"]], last["next"]) == (["CA_1"], None)


async def test_a_sandbox_call_is_not_on_a_production_keys_list(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_call(store, "CA_test", env="sandbox")
    assert got(gateway, "/v1/sessions")[1] == {"calls": [], "total": 0, "next": None}

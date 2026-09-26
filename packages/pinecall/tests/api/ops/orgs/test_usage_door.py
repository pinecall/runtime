"""GET /v1/ops/usage: exactly the rows call.summary carries, per org, and a cursor that resumes."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from pinecall.log.store import MemoryStore
from pinecall_testkit.usage import A_SCORE, A_SUMMARY
from tests.api.conftest import A_RECORD, AGENT, over_the_asgi_app

pytestmark = pytest.mark.unit

USAGE = "/v1/ops/usage"
ORG = A_RECORD.org


async def three_calls(store: MemoryStore) -> None:
    """Two summarised calls and one scored, with the conversation entries nobody meters between."""
    for call in ("CA_1", "CA_2"):
        await store.owned(call, AGENT, ORG)
        await store.append(call, AGENT, "call.started", {})
        await store.append(call, AGENT, "turn.user", {"text": "hola"})
        await store.append(call, AGENT, "call.summary", A_SUMMARY)
    await store.append("CA_2", AGENT, "call.score", A_SCORE)


async def a_page(ops_http: httpx.AsyncClient, **params: Any) -> dict[str, Any]:
    answer = await ops_http.get(USAGE, params=params)
    assert answer.status_code == 200, answer.text
    body: dict[str, Any] = answer.json()
    return body


async def test_the_rows_are_the_logs_own_summaries_and_scores_and_nothing_else(
    ops_http: httpx.AsyncClient, store: MemoryStore
) -> None:
    """Criterion 3: what call.summary already carries, folded, and the conversation left out."""
    await three_calls(store)
    page = await a_page(ops_http)
    assert [(row["call"], row["type"]) for row in page["rows"]] == [
        ("CA_1", "call.summary"),
        ("CA_2", "call.summary"),
        ("CA_2", "call.score"),
    ]
    first = page["rows"][0]
    assert (first["org"], first["agent"], first["minutes"], first["messages"]) == (
        ORG,
        AGENT,
        1.5,
        6,
    )
    assert (first["input_tokens"], first["output_tokens"], first["characters"]) == (1200, 300, 450)
    assert page["rows"][2]["judge_calls"] == 2
    assert page["totals"][ORG]["calls"] == 2
    assert page["totals"][ORG]["minutes"] == pytest.approx(3.0)


async def test_the_cursor_resumes_exactly_where_the_last_page_ended(
    ops_http: httpx.AsyncClient, store: MemoryStore
) -> None:
    """Criterion 3: `after` is the position of the last row read, and the end is `next: null`."""
    await three_calls(store)
    whole = await a_page(ops_http)
    cursors = [row["cursor"] for row in whole["rows"]]
    assert cursors == sorted(cursors) and whole["next"] == cursors[-1]
    rest = await a_page(ops_http, after=cursors[0])
    assert [row["call"] for row in rest["rows"]] == ["CA_2", "CA_2"]
    end = await a_page(ops_http, after=whole["next"])
    assert end == {"rows": [], "totals": {}, "next": None}
    await store.append("CA_1", AGENT, "call.score", A_SCORE)
    later = await a_page(ops_http, after=whole["next"])
    assert [(row["call"], row["type"]) for row in later["rows"]] == [("CA_1", "call.score")]


async def test_a_page_of_another_orgs_rows_still_moves_the_cursor(
    ops_http: httpx.AsyncClient, store: MemoryStore
) -> None:
    """Filtered out is not unread: a cloud asking for one org never re-reads everybody else's."""
    await three_calls(store)
    page = await a_page(ops_http, org="default")
    assert page["rows"] == [] and page["next"] is not None


async def test_the_usage_door_takes_the_ops_key_and_nothing_else(
    ops_http: httpx.AsyncClient,  # noqa: ARG001 — the wiring; the knock below carries no key
) -> None:
    async with over_the_asgi_app("") as nobody:
        assert (await nobody.get(USAGE)).status_code == 401

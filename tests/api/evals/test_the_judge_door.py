"""POST /v1/evals/judge/{call}: a call nobody judged, judged on ask and written to its log."""

from __future__ import annotations

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.api.calls.log_sink import NO_SUCH_CALL
from pinecall.api.evals.judge import ALREADY_JUDGED, STILL_GOING
from pinecall.auth.keys import NOT_OPENED, KeyRecord, MemoryKeys
from pinecall.evals.hangup_score import JUDGING_OFF
from pinecall.log.store import MemoryStore
from tests.api.conftest import A_KEY, A_RECORD, over_the_asgi_app
from tests.api.evals.conftest import BOOK, CONFIRMED, entries_of, serving

pytestmark = pytest.mark.unit

DOOR = "/v1/evals/judge/{call}"
A_QA_KEY = "pk_test_reads_calls_only"
A_READER = KeyRecord(key_id="k_read", org=A_RECORD.org, scopes=frozenset({"calls"}))
THE_SHOPS_KEY = "pk_test_the_shop"
THE_SHOP = KeyRecord(key_id="k_shop", org="tienda")


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, A_QA_KEY: A_READER, THE_SHOPS_KEY: THE_SHOP})


async def a_finished_call(store: MemoryStore, *, judged: bool = False, over: bool = True) -> str:
    """The fixture booking, claimed by the clinic, sealed with a call.score that has no verdict."""
    entries = entries_of(CONFIRMED)
    call = entries[0].call or ""
    await store.owned(call, entries[0].agent, A_RECORD.org, "production", "")
    for entry in entries:
        if entry.type == "call.ended" and not over:
            break
        await store.append(entry.call, entry.agent, entry.type, entry.data, entry.ephemeral)
    if over:
        verdict = {"passed": True} if judged else {"not_judged": JUDGING_OFF}
        await store.append(
            call, entries[0].agent, "call.score", {**verdict, "judges": [], "judge_calls": 0}
        )
        await store.seal(call)
    return call


async def test_a_call_its_org_did_not_judge_is_judged_and_the_verdict_lands_on_its_log(
    keyed_http: httpx.AsyncClient, store: MemoryStore, registry: Registry
) -> None:
    call = await a_finished_call(store)
    await serving(registry, tools=[BOOK])
    answered = await keyed_http.post(DOOR.format(call=call))
    assert answered.status_code == 200
    body = answered.json()
    assert body["panel"] == ["consent", "grounded", "promises"]
    assert next(row["name"] for row in body["judges"]) == "consent"
    scores = [entry for entry in await store.since(call) if entry.type == "call.score"]
    written = scores[-1].data
    assert len(scores) == 2
    assert [(row["name"], row["verdict"]) for row in written["judges"]] == [
        (row["name"], row["verdict"]) for row in body["judges"]
    ]
    facts = (await store.facts_of([call]))[call]
    assert facts.judged is not None and facts.judged > 0


async def test_a_call_already_judged_is_judged_again_only_when_asked_to(
    keyed_http: httpx.AsyncClient, store: MemoryStore
) -> None:
    call = await a_finished_call(store, judged=True)
    refused = await keyed_http.post(DOOR.format(call=call))
    assert (refused.status_code, refused.json()["detail"]) == (
        409,
        ALREADY_JUDGED.format(call=call),
    )
    assert (await keyed_http.post(f"{DOOR.format(call=call)}?again=true")).status_code == 200


async def test_a_call_still_going_is_not_judged(
    keyed_http: httpx.AsyncClient, store: MemoryStore
) -> None:
    call = await a_finished_call(store, over=False)
    refused = await keyed_http.post(DOOR.format(call=call))
    assert (refused.status_code, refused.json()["detail"]) == (409, STILL_GOING.format(call=call))


async def test_another_orgs_call_and_nobodys_call_are_the_same_404(
    keyed_http: httpx.AsyncClient, store: MemoryStore
) -> None:
    call = await a_finished_call(store)
    shop = over_the_asgi_app(f"Bearer {THE_SHOPS_KEY}")
    try:
        theirs = await shop.post(DOOR.format(call=call))
    finally:
        await shop.aclose()
    nobody = await keyed_http.post(DOOR.format(call="CA_nobody"))
    assert (theirs.status_code, theirs.json()["detail"]) == (404, NO_SUCH_CALL.format(call=call))
    assert nobody.status_code == 404


async def test_a_key_without_evals_is_refused(
    store: MemoryStore,
    wired: None,  # noqa: ARG001
) -> None:
    call = await a_finished_call(store)
    reader = over_the_asgi_app(f"Bearer {A_QA_KEY}")
    try:
        refused = await reader.post(DOOR.format(call=call))
    finally:
        await reader.aclose()
    assert refused.json()["detail"] == NOT_OPENED.format(scope="evals", opens="calls")

"""Criterion 2: POST /v1/evals/replay/{call} over the real ASGI app, on the one bearer parser."""

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.api.calls.log_sink import NO_SUCH_CALL
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.log.store import MemoryStore
from tests.api.conftest import A_KEY, A_RECORD, over_the_asgi_app
from tests.api.evals.conftest import AGENT, BOOK, CONFIRMED, NO_GATE, entries_of, serving

pytestmark = pytest.mark.unit

DOOR = "/v1/evals/replay/{call}"
THE_SHOPS_KEY = "pk_test_the_shop"
THE_SHOP = KeyRecord(key_id="k_shop", org="tienda")


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, THE_SHOPS_KEY: THE_SHOP})


async def written(store: MemoryStore, name: str) -> str:
    """One fixture log appended to this gateway's store, as a worker would have written it:
    claimed for the clinic's own corner first, the way POST /v1/calls claims every call."""
    entries = entries_of(name)
    call = entries[0].call or ""
    await store.owned(call, entries[0].agent, A_RECORD.org, "production", "")
    for entry in entries:
        await store.append(entry.call, entry.agent, entry.type, entry.data, entry.ephemeral)
    return call


async def test_the_door_answers_the_four_verdicts_of_a_call_that_consented(
    keyed_http: httpx.AsyncClient, store: MemoryStore, registry: Registry
) -> None:
    """The round trip: the log in the store, the declaration in the registry, four verdicts."""
    call = await written(store, CONFIRMED)
    await serving(registry, tools=[BOOK])

    answered = await keyed_http.post(DOOR.format(call=call), json={"banned": ["te", "tienes"]})

    assert answered.status_code == httpx.codes.OK
    body = answered.json()
    assert body["call"] == call
    assert body["agent"] == AGENT
    assert body["passed"] is True
    assert {verdict["check"]: verdict["status"] for verdict in body["verdicts"]} == {
        "consent": "held",
        "register": "held",
        "errors": "held",
        "latency": "held",
    }


async def test_a_call_this_runtime_wrote_today_reads_deferred_and_still_passes(
    keyed_http: httpx.AsyncClient, store: MemoryStore, registry: Registry
) -> None:
    """The gate is deferred, so an irreversible tool with no confirm.* must not fail the call."""
    call = await written(store, NO_GATE)
    await serving(registry, tools=[BOOK])

    body = (await keyed_http.post(DOOR.format(call=call), json=None)).json()

    verdicts = {verdict["check"]: verdict for verdict in body["verdicts"]}
    assert verdicts["consent"]["status"] == "deferred"
    assert verdicts["register"]["status"] == "skipped"
    assert body["passed"] is True


async def test_an_id_nobody_wrote_under_is_a_404_and_never_a_call_that_passed(
    keyed_http: httpx.AsyncClient,
) -> None:
    """A typo answering `passed: true` is the worst answer this door could give."""
    refused = await keyed_http.post(DOOR.format(call="CA_nope"), json=None)
    assert refused.status_code == httpx.codes.NOT_FOUND
    assert refused.json()["detail"] == NO_SUCH_CALL.format(call="CA_nope")


async def test_another_orgs_call_is_the_same_404_as_nobodys(
    keyed_http: httpx.AsyncClient, store: MemoryStore, registry: Registry
) -> None:
    """The judge door's rule, at this door too: another tenant's call reads as no call at all."""
    call = await written(store, CONFIRMED)
    await serving(registry, tools=[BOOK])
    shop = over_the_asgi_app(f"Bearer {THE_SHOPS_KEY}")
    try:
        theirs = await shop.post(DOOR.format(call=call), json=None)
    finally:
        await shop.aclose()
    ours = await keyed_http.post(DOOR.format(call=call), json=None)
    assert (theirs.status_code, theirs.json()["detail"]) == (404, NO_SUCH_CALL.format(call=call))
    assert ours.status_code == httpx.codes.OK


async def test_the_door_is_closed_to_a_request_with_no_key(
    keyed_http: httpx.AsyncClient, store: MemoryStore
) -> None:
    """The same 401 every other door answers, out of the same bearer parser, with no key at all."""
    call = await written(store, CONFIRMED)
    del keyed_http.headers["Authorization"]

    refused = await keyed_http.post(DOOR.format(call=call), json=None)

    assert refused.status_code == httpx.codes.UNAUTHORIZED

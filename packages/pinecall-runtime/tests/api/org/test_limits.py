"""GET /v1/limits: each quota as {limit, used}, the lending, and where the box's orgs pay."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from pinecall.log.store import MemoryStore
from pinecall.orgs.records_memory import MemoryOrgs
from pinecall.settings import Settings
from pinecall.types import Quotas
from tests.api.conftest import A_LIVEKIT, A_RECORD, A_VAULT_KEY, AGENT, AN_OPS_KEY
from tests.api.talking import got
from tests.log.test_usage import A_SUMMARY

pytestmark = pytest.mark.unit

ORG = A_RECORD.org
THE_PLANS = "https://pinecall.io/billing"


@pytest.fixture
def settings() -> Settings:
    """A box that bills: its orgs pay at a page of whoever charges for it."""
    return Settings(
        world="production",
        ops_key=AN_OPS_KEY,
        livekit_api_key=A_LIVEKIT.api_key,
        livekit_api_secret=A_LIVEKIT.api_secret,
        vault_key=A_VAULT_KEY,
        billing_url=THE_PLANS,
    )


async def test_a_trial_reads_its_minutes_tokens_and_lending_as_the_gate_counts_them(
    gateway: TestClient, orgs: MemoryOrgs, store: MemoryStore
) -> None:
    await orgs.set_quotas(
        ORG, Quotas(minutes=30, llm_tokens=2_000_000, lends=frozenset({"deepgram", "cartesia"}))
    )
    await store.owned("CA_first", AGENT, ORG)
    await store.append("CA_first", AGENT, "call.summary", A_SUMMARY)
    status, body = got(gateway, "/v1/limits")
    assert status == 200
    assert body["minutes"] == {"limit": 30, "used": 1.5}
    assert body["llm_tokens"] == {"limit": 2_000_000, "used": 1500}
    assert body["messages"] == {"limit": None, "used": 6}
    assert body["lends"] == ["cartesia", "deepgram"]
    assert body["billing_url"] == THE_PLANS


async def test_an_org_nobody_limited_reads_no_limit_anywhere_and_every_key_lent(
    gateway: TestClient,
) -> None:
    status, body = got(gateway, "/v1/limits")
    assert status == 200
    quotas = ("minutes", "messages", "llm_tokens", "concurrent_calls", "agents", "seats", "numbers")
    assert all(body[name]["limit"] is None for name in quotas)
    assert body["lends"] is None


def test_the_door_takes_a_key_and_nothing_less(gateway: TestClient) -> None:
    status, _ = got(gateway, "/v1/limits", bearer=None)
    assert status == 401

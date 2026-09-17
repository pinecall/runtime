"""Whether an org's calls are judged: read with `calls`, turned with `usage`, asked by a worker."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall._settings import Settings
from pinecall.api.agents.registry import Registry
from pinecall.auth.keys import NOT_OPENED, KeyRecord, MemoryKeys
from pinecall.evals.score import JUDGING_OFF, JudgedWhen
from pinecall.orgs.table import MemoryOrgs
from pinecall.types import AgentConfig
from pinecall.worker.client import Gateway
from tests.api.calls.test_lookup import CALL, a_phone_call
from tests.api.conftest import A_KEY, A_RECORD, AGENT, AN_ORG, over_the_asgi_app
from tests.api.talking import got

pytestmark = pytest.mark.unit

JUDGING = "/v1/org/judging"
A_QA_KEY = "pk_test_qa_reads"
A_QA = KeyRecord(key_id="k_qa", org=A_RECORD.org, scopes=frozenset({"calls", "evals"}))
THE_SHOPS_KEY = "pk_test_the_shop_next_door"
THE_SHOP = KeyRecord(key_id="k_shop", org="tienda")


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, A_QA_KEY: A_QA, THE_SHOPS_KEY: THE_SHOP})


def put(gateway: TestClient, on: bool, bearer: str = A_KEY) -> tuple[int, Any]:
    handle: Any = gateway
    answer: Any = handle.put(
        JUDGING, json={"on": on}, headers={"Authorization": f"Bearer {bearer}"}
    )
    return answer.status_code, answer.json()


def test_an_org_nobody_asked_about_is_judged_under_the_boxs_ceiling(
    gateway: TestClient, settings: Settings
) -> None:
    ceiling = settings.judge_ceiling_eur
    assert got(gateway, JUDGING, A_QA_KEY) == (200, {"on": True, "ceiling_eur": ceiling})


def test_judging_is_turned_off_for_one_org_and_its_neighbour_is_still_judged(
    gateway: TestClient, settings: Settings
) -> None:
    assert put(gateway, False) == (200, {"on": False, "ceiling_eur": settings.judge_ceiling_eur})
    assert got(gateway, JUDGING)[1]["on"] is False
    assert got(gateway, JUDGING, THE_SHOPS_KEY)[1]["on"] is True
    assert put(gateway, True)[1]["on"] is True


def test_a_key_that_only_reads_calls_may_not_turn_it(gateway: TestClient) -> None:
    status, body = put(gateway, False, A_QA_KEY)
    assert (status, body["detail"]) == (
        403,
        NOT_OPENED.format(scope="usage", opens="calls · evals"),
    )


async def test_the_worker_asks_about_the_call_it_is_sealing_and_another_org_is_told_nothing(
    worker_gateway: Gateway, registry: Registry, orgs: MemoryOrgs
) -> None:
    await a_phone_call(worker_gateway, registry)
    assert await worker_gateway.judging(CALL) is True
    await orgs.set_judging(AN_ORG.id, False)
    assert await worker_gateway.judging(CALL) is False
    shop = over_the_asgi_app(f"Bearer {THE_SHOPS_KEY}")
    try:
        answer = await shop.get(f"/v1/calls/{CALL}/judging")
    finally:
        await shop.aclose()
    assert answer.status_code == 404


async def test_a_call_whose_org_does_not_judge_is_sealed_with_the_reason_and_no_verdict() -> None:
    async def not_judged(_call: str) -> bool:
        return False

    scored = await JudgedWhen(not_judged)([], AgentConfig(slug=AGENT))
    assert (scored.judges, scored.passed, scored.not_judged) == ([], None, JUDGING_OFF)

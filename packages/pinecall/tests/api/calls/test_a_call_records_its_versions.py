"""A call's head row says which tuning and which lexicon it ran on, and a door reads them back."""

import asyncio

import pytest
from starlette.testclient import TestClient

from pinecall.log.store import MemoryStore
from pinecall.orgs.tuning_store_memory import MemoryTuning
from pinecall.types import PRODUCTION, Lexicon, Model, Tuning
from tests.api.conftest import A_RECORD, AGENT
from tests.api.talking import a_call_the_app_ends, an_app, declared, got

pytestmark = pytest.mark.unit

SONNET = "anthropic/claude-sonnet-4-5"


def test_a_text_call_records_the_versions_it_was_built_on_and_a_door_reads_them_back(
    gateway: TestClient,
    tuning: MemoryTuning,
    store: MemoryStore,
    models_asked: list[Model | None],
) -> None:
    asyncio.run(
        tuning.put(
            A_RECORD.org,
            PRODUCTION,
            "",
            AGENT,
            Tuning(llm=SONNET),
            author="m_ana",
            note=None,
            if_version=None,
        )
    )
    asyncio.run(
        tuning.put_lexicon(
            A_RECORD.org,
            PRODUCTION,
            "",
            Lexicon(said={"GSA": "G S A"}),
            author="m_carla",
            note=None,
            if_version=None,
        )
    )
    with an_app(gateway) as app_socket:
        declared(app_socket)
        call = a_call_the_app_ends(gateway, app_socket)
    assert models_asked[-1] == Model(provider="anthropic", model="claude-sonnet-4-5")
    corner = asyncio.run(store.corner_of_call(call))
    assert corner is not None and (corner.config_version, corner.lexicon_version) == (1, 1)
    status, body = got(gateway, f"/v1/calls/{call}/settings")
    assert status == 200, body
    assert (body["config_version"], body["lexicon_version"]) == (1, 1)
    assert body["config"]["config"]["llm"] == SONNET and body["config"]["author"] == "m_ana"
    assert body["lexicon"]["lexicon"]["said"] == [{"word": "GSA", "spoken": "G S A"}]


def test_a_call_with_nothing_set_records_null_and_the_door_says_so(
    gateway: TestClient, store: MemoryStore
) -> None:
    with an_app(gateway) as app_socket:
        declared(app_socket)
        call = a_call_the_app_ends(gateway, app_socket)
    corner = asyncio.run(store.corner_of_call(call))
    assert corner is not None and (corner.config_version, corner.lexicon_version) == (None, None)
    status, body = got(gateway, f"/v1/calls/{call}/settings")
    assert status == 200
    assert body == {
        "config_version": None,
        "lexicon_version": None,
        "config": None,
        "lexicon": None,
    }
    assert got(gateway, "/v1/calls/CA_nobody/settings")[0] == 404

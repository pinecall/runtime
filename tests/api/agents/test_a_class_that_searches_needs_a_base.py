"""A class that searches knowledge itself is refused at configure when this world attaches none."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.api.agents.socket import NO_BASE_ATTACHED
from pinecall.orgs.tuning import MemoryTuning
from pinecall.types import PRODUCTION, Docs, Tuning
from tests.api.conftest import A_RECORD, AGENT
from tests.api.talking import a_door, a_frame, a_register, an_app

pytestmark = pytest.mark.unit

SEARCHES = {"language": "es", "uses_knowledge": True}


def configured(gateway: TestClient, config: Mapping[str, object]) -> dict[str, Any]:
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        app_socket.send_json(a_frame("agent.configure", AGENT, {"config": config}, id="cmd_1"))
        answer: dict[str, Any] = app_socket.receive_json()
        return answer


def test_searching_with_nothing_attached_is_refused_naming_the_verb(gateway: TestClient) -> None:
    answer = configured(gateway, SEARCHES)
    assert answer["type"] == "error"
    assert answer["data"]["code"] == "refused"
    assert answer["data"]["message"] == NO_BASE_ATTACHED.format(slug=AGENT, world=PRODUCTION)
    assert "pinecall docs attach <base> --agent" in answer["data"]["message"]


def test_a_base_the_world_attached_lets_the_class_search(
    gateway: TestClient, tuning: MemoryTuning
) -> None:
    asyncio.run(
        tuning.put(
            A_RECORD.org,
            PRODUCTION,
            "",
            AGENT,
            Tuning(bases=(Docs(base="clinica"),)),
            author="k_1",
            note=None,
            if_version=None,
        )
    )
    assert configured(gateway, SEARCHES)["type"] != "error"

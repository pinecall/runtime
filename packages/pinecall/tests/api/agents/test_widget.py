"""How the widget presents an agent: read with `talk`, set with `pipeline`, in the key's world."""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

from pinecall.auth.keys import NOT_OPENED, KeyRecord
from pinecall.auth.keys_memory import MemoryKeys
from pinecall.settings import Settings
from pinecall.types import SANDBOX
from tests.api.conftest import A_KEY, A_RECORD, AGENT
from tests.api.talking import answering_in, got

pytestmark = pytest.mark.unit

WIDGET = f"/v1/agents/{AGENT}/widget"
NOTHING_SET = {
    "title": None,
    "tagline": None,
    "greeting": None,
    "accent": None,
    "autostart": False,
    "theme": None,
}
A_WIDGET = {
    "title": "Clínica Norte",
    "tagline": "Le atendemos ahora",
    "greeting": "Hola, ¿en qué le ayudo?",
    "accent": "#cd58b2",
    "autostart": True,
    "theme": "dark",
}
A_TALKER_KEY = "pk_test_talks"
A_TALKER = KeyRecord(key_id="k_talk", org=A_RECORD.org, scopes=frozenset({"talk"}))
A_SANDBOX_KEY = "pk_test_the_sandbox"
A_SANDBOX = KeyRecord(key_id="k_sand", org=A_RECORD.org, env="sandbox")
THE_SHOPS_KEY = "pk_test_the_shop"
THE_SHOP = KeyRecord(key_id="k_shop", org="tienda")


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys(
        {A_KEY: A_RECORD, A_TALKER_KEY: A_TALKER, A_SANDBOX_KEY: A_SANDBOX, THE_SHOPS_KEY: THE_SHOP}
    )


def put(gateway: TestClient, body: Any, bearer: str = A_KEY) -> Any:
    handle: Any = gateway
    return handle.put(WIDGET, json=body, headers={"Authorization": f"Bearer {bearer}"})


def test_an_agent_nobody_set_a_widget_for_answers_the_widgets_defaults(gateway: TestClient) -> None:
    assert got(gateway, WIDGET, A_TALKER_KEY) == (200, NOTHING_SET)


def test_a_widget_is_kept_for_its_org_and_world_alone(
    gateway: TestClient, settings: Settings
) -> None:
    assert (put(gateway, A_WIDGET).status_code, put(gateway, A_WIDGET).json()) == (200, A_WIDGET)
    assert got(gateway, WIDGET, A_TALKER_KEY) == (200, A_WIDGET)
    assert got(gateway, WIDGET, THE_SHOPS_KEY)[1] == NOTHING_SET
    answering_in(SANDBOX, settings)
    assert got(gateway, WIDGET, A_SANDBOX_KEY)[1] == NOTHING_SET


def test_a_colour_that_could_close_a_declaration_is_refused(gateway: TestClient) -> None:
    refused = put(gateway, {**A_WIDGET, "accent": "red; background: url(x)"})
    assert refused.status_code == 400 and "CSS colour" in refused.json()["detail"]
    assert put(gateway, {**A_WIDGET, "greeting": "x" * 501}).status_code == 400


def test_a_theme_is_auto_light_or_dark(gateway: TestClient) -> None:
    assert put(gateway, {**A_WIDGET, "theme": "sepia"}).status_code == 422


def test_a_console_that_predates_the_theme_sets_the_widgets_default(gateway: TestClient) -> None:
    older = {field: value for field, value in A_WIDGET.items() if field != "theme"}
    assert put(gateway, older).json() == {**older, "theme": None}


def test_setting_takes_pipeline(gateway: TestClient) -> None:
    refused = put(gateway, A_WIDGET, A_TALKER_KEY)
    assert refused.json()["detail"] == NOT_OPENED.format(scope="pipeline", opens="talk")

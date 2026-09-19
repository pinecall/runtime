"""An agent's settings over the real ASGI app: three corners, the version gate, words, the shim."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.auth.keys import NOT_OPENED, KeyRecord, MemoryKeys
from pinecall.auth.world import ENV_HEADER, NO_PRODUCTION
from pinecall.types import BLANK, ROLE_SCOPES, SANDBOX
from pinecall_protocol import defs
from tests.api.conftest import A_KEY, A_RECORD, AGENT, PIPELINE_KNOBS, over_the_asgi_app
from tests.api.pipeline.conftest import declared

pytestmark = pytest.mark.unit

SETTINGS = f"/v1/agents/{AGENT}/settings"
CONFIG = f"/v1/agents/{AGENT}/config"

# Three people of the clinic in the sandbox — two developers, each with a corner of their own,
# and a supervisor, whose key opens words and holds no agent — and the org's own CI key.
ANA_KEY = "pk_test_ana_writes_the_agent"
ANA = KeyRecord(
    key_id="k_ana",
    org=A_RECORD.org,
    env=SANDBOX,
    scopes=ROLE_SCOPES["developer"],
    subject="m_ana",
    name="Ana",
)
BRUNO_KEY = "pk_test_bruno_writes_it_too"
BRUNO = KeyRecord(
    key_id="k_bruno",
    org=A_RECORD.org,
    env=SANDBOX,
    scopes=ROLE_SCOPES["developer"],
    subject="m_bruno",
    name="Bruno",
)
CARLA_KEY = "pk_test_carla_hears_the_calls"
CARLA = KeyRecord(
    key_id="k_carla",
    org=A_RECORD.org,
    env=SANDBOX,
    scopes=ROLE_SCOPES["supervisor"],
    subject="m_carla",
    name="Carla",
)
CI_KEY = "pk_test_the_orgs_own_ci"
CI = KeyRecord(key_id="k_ci", org=A_RECORD.org, env=SANDBOX, scopes=ROLE_SCOPES["developer"])

SONNET = {"llm": "anthropic/claude-sonnet-4-5"}
HAIKU = {"llm": "anthropic/claude-haiku-4-5"}


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys(
        {A_KEY: A_RECORD, ANA_KEY: ANA, BRUNO_KEY: BRUNO, CARLA_KEY: CARLA, CI_KEY: CI}
    )


@pytest.fixture
async def ana(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = over_the_asgi_app(f"Bearer {ANA_KEY}")
    yield http
    await http.aclose()


@pytest.fixture
async def bruno(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = over_the_asgi_app(f"Bearer {BRUNO_KEY}")
    yield http
    await http.aclose()


@pytest.fixture
async def carla(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = over_the_asgi_app(f"Bearer {CARLA_KEY}")
    yield http
    await http.aclose()


@pytest.fixture
async def ci(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = over_the_asgi_app(f"Bearer {CI_KEY}")
    yield http
    await http.aclose()


async def in_the_sandbox(registry: Registry) -> None:
    """The clinic on air in the org's own sandbox corner, as CI's process would hold it."""
    await registry.register(
        "app_ci", A_RECORD.org, SANDBOX, AGENT, [defs.Route(channel="web", number=None)]
    )
    await registry.configure(
        "app_ci",
        SANDBOX,
        AGENT,
        defs.AgentConfig(
            language="es",
            llm=defs.ModelConfig(provider="anthropic", model="claude-haiku-4-5"),
            says=[defs.Pronunciation(word="Vidal", spoken="bidál")],
        ),
    )


# ── the corners ─────────────────────────────────────────────────────────────────


async def test_a_set_lands_in_your_own_corner_and_a_colleague_does_not_hear_it(
    ana: httpx.AsyncClient, bruno: httpx.AsyncClient
) -> None:
    put = await ana.put(SETTINGS, json={"config": SONNET})
    assert put.status_code == 200, put.text
    assert put.json()["yours"]["version"] == 1 and put.json()["team"] is None
    seen = (await bruno.get(SETTINGS)).json()
    assert (seen["yours"], seen["team"], seen["production"]) == (None, None, None)


async def test_the_team_flag_writes_the_orgs_own_corner_which_every_corner_falls_back_to(
    ana: httpx.AsyncClient, bruno: httpx.AsyncClient, registry: Registry
) -> None:
    await in_the_sandbox(registry)
    put = await ana.put(SETTINGS, json={"config": SONNET, "team": True, "note": "cleaner"})
    assert put.status_code == 200, put.text
    assert put.json()["yours"] is None
    team = put.json()["team"]
    assert (team["version"], team["holder"], team["author"], team["note"]) == (
        1,
        "",
        "m_ana",
        "cleaner",
    )
    # Bruno set nothing of his own, so the session built in his corner runs the team's model.
    config = (await bruno.get(CONFIG)).json()
    assert config["llm"] == {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5",
        "temperature": None,
    }


async def test_a_key_that_names_nobody_writes_the_orgs_own_corner(ci: httpx.AsyncClient) -> None:
    put = await ci.put(SETTINGS, json={"config": SONNET})
    assert put.status_code == 200, put.text
    assert put.json()["yours"] is None and put.json()["team"]["version"] == 1


# ── the version gate ────────────────────────────────────────────────────────────


async def test_two_saves_that_read_the_same_version_do_not_both_win(
    ana: httpx.AsyncClient,
) -> None:
    assert (await ana.put(SETTINGS, json={"config": SONNET})).json()["yours"]["version"] == 1
    second = await ana.put(SETTINGS, json={"config": HAIKU, "if_version": 1})
    assert second.json()["yours"]["version"] == 2
    stale = await ana.put(SETTINGS, json={"config": SONNET, "if_version": 1})
    assert stale.status_code == 409
    assert "v2" in stale.json()["detail"]
    assert (await ana.get(SETTINGS)).json()["yours"]["config"]["llm"] == HAIKU["llm"]


# ── production ──────────────────────────────────────────────────────────────────


async def test_a_key_that_acts_in_production_sets_production_there_and_nowhere_else(
    fleet_http: httpx.AsyncClient,
) -> None:
    put = await fleet_http.put(SETTINGS, json={"config": SONNET})
    assert put.status_code == 200, put.text
    assert put.json()["production"]["version"] == 1
    assert put.json()["production"]["config"]["llm"] == SONNET["llm"]


async def test_a_person_the_org_keeps_out_of_production_is_refused_there_by_name(
    ana: httpx.AsyncClient,
) -> None:
    refused = await ana.put(SETTINGS, json={"config": SONNET}, headers={ENV_HEADER: "production"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == NO_PRODUCTION.format(name="Ana")


# ── words ───────────────────────────────────────────────────────────────────────


async def test_a_words_key_sets_the_opening_and_carries_the_pipeline_over_untouched(
    ana: httpx.AsyncClient, carla: httpx.AsyncClient
) -> None:
    await ana.put(SETTINGS, json={"config": SONNET, "team": True})
    put = await carla.put(
        SETTINGS, json={"config": {"greeting": {"say": "Buenas, Clínica Norte."}}}
    )
    assert put.status_code == 200, put.text
    team = put.json()["team"]
    assert (team["version"], team["author"]) == (2, "m_carla")
    assert team["config"]["llm"] == SONNET["llm"]
    assert team["config"]["greeting"] == {
        "say": "Buenas, Clínica Norte.",
        "reply": None,
        "allow_interruptions": None,
    }


async def test_a_words_key_is_refused_a_vendor_by_name_and_the_scope_it_lacks(
    carla: httpx.AsyncClient,
) -> None:
    refused = await carla.put(SETTINGS, json={"config": {"llm": "openai/gpt-5"}})
    assert refused.status_code == 403
    assert refused.json()["detail"].startswith("llm: the pipeline's")
    assert (
        NOT_OPENED.format(scope="pipeline", opens=" · ".join(sorted(CARLA.scopes)))
        in refused.json()["detail"]
    )
    instructed = await carla.put(SETTINGS, json={"config": {"greeting": {"reply": "saluda"}}})
    assert instructed.status_code == 403 and "greeting.reply" in instructed.json()["detail"]


# ── history, diff, rollback ─────────────────────────────────────────────────────


async def test_history_diff_and_rollback_over_the_teams_corner(ana: httpx.AsyncClient) -> None:
    await ana.put(SETTINGS, json={"config": SONNET, "team": True})
    await ana.put(SETTINGS, json={"config": HAIKU, "team": True, "if_version": 1})
    history = (await ana.get(f"{SETTINGS}/history", params={"team": "true"})).json()
    assert history["holder"] == "" and [row["version"] for row in history["rows"]] == [2, 1]
    diff = (await ana.get(f"{SETTINGS}/diff", params={"against": "team"})).json()
    assert diff["ours"]["version"] == 2 and diff["theirs"]["version"] == 2 and diff["changed"] == []
    against = (await ana.get(f"{SETTINGS}/diff")).json()
    assert against["theirs"] is None and against["changed"] == ["llm"]
    back = await ana.post(f"{SETTINGS}/rollback", json={"version": 1, "team": True})
    assert back.status_code == 200, back.text
    newest = back.json()["team"]
    assert (newest["version"], newest["note"], newest["config"]["llm"]) == (
        3,
        "rollback to v1",
        SONNET["llm"],
    )
    missing = await ana.post(f"{SETTINGS}/rollback", json={"version": 9, "team": True})
    assert missing.status_code == 404


# ── the shape's own rules, at the door ──────────────────────────────────────────


async def test_a_blank_knob_is_refused_in_the_shapes_own_sentence(ana: httpx.AsyncClient) -> None:
    refused = await ana.put(SETTINGS, json={"config": {"voice": "   "}})
    assert (refused.status_code, refused.json()["detail"]) == (400, BLANK.format(field="voice"))


async def test_a_vendor_this_build_has_no_file_for_is_refused_before_it_is_kept(
    ana: httpx.AsyncClient,
) -> None:
    refused = await ana.put(SETTINGS, json={"config": {"llm": "misspelt/gpt-5"}})
    assert refused.status_code == 400 and "no llm vendor named" in refused.json()["detail"]
    assert (await ana.get(SETTINGS)).json()["yours"] is None


# ── the six-knob door, kept one release ─────────────────────────────────────────


async def test_the_old_knobs_door_writes_a_version_of_the_same_store(
    fleet_http: httpx.AsyncClient, registry: Registry
) -> None:
    await declared(registry)
    turned = await fleet_http.put(PIPELINE_KNOBS, json={"llm": SONNET["llm"]})
    assert turned.status_code == 200, turned.text
    assert turned.json()["overrides"]["llm"] == SONNET["llm"]
    production = (await fleet_http.get(SETTINGS)).json()["production"]
    assert (production["version"], production["note"]) == (1, "pipeline/overrides")
    assert production["config"]["llm"] == SONNET["llm"]

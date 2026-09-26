"""An agent's settings over the real ASGI app: three corners, the version gate, words."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.api.agents.registry import Registry
from pinecall.auth.env import ENV_HEADER, NO_PRODUCTION
from pinecall.auth.keys import NOT_OPENED, KeyRecord, MemoryKeys
from pinecall.orgs.records import MemoryOrgs
from pinecall.orgs.vault import Vault
from pinecall.types import BLANK, PRODUCTION, ROLE_SCOPES, SANDBOX, Quotas
from pinecall_protocol import defs
from tests.api.conftest import A_KEY, A_RECORD, AGENT
from tests.api.talking import answering_in, at_the_console
from tests.conftest import a_sandbox

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

# ElevenLabs' Sarah, as providers/tts/curated_voices.py curates her: what a voice knob resolves to.
CAROLINA = "EXAVITQu4vr4xnSDxMaL"
SONNET = {"llm": "anthropic/claude-sonnet-4-5"}
HAIKU = {"llm": "anthropic/claude-haiku-4-5"}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The sandbox's instance, where the corners are; production's tests ask for it by name."""
    return a_sandbox(settings)


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys(
        {A_KEY: A_RECORD, ANA_KEY: ANA, BRUNO_KEY: BRUNO, CARLA_KEY: CARLA, CI_KEY: CI}
    )


@pytest.fixture
async def ana(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = at_the_console(ANA_KEY, SANDBOX)
    yield http
    await http.aclose()


@pytest.fixture
async def bruno(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = at_the_console(BRUNO_KEY, SANDBOX)
    yield http
    await http.aclose()


@pytest.fixture
async def carla(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = at_the_console(CARLA_KEY, SANDBOX)
    yield http
    await http.aclose()


@pytest.fixture
async def ci(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = at_the_console(CI_KEY, SANDBOX)
    yield http
    await http.aclose()


async def in_the_sandbox(registry: Registry) -> None:
    """The clinic on air in the org's own sandbox corner, as CI's process would hold it."""
    await registry.register("app_ci", A_RECORD.org, SANDBOX, AGENT)
    await registry.configure(
        "app_ci",
        SANDBOX,
        AGENT,
        defs.AgentConfig(language="es"),
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


# Berna's worked example over the doors, on the path a call really takes: `GET .../config` is what
# the worker builds the session from, so this is the effective tuning and not a screen's view of it.
async def test_a_voice_of_your_own_keeps_the_teams_model_and_ear(
    ana: httpx.AsyncClient, registry: Registry
) -> None:
    """Setting one knob used to take the whole row: the team's llm, stt and the rest went unset."""
    await in_the_sandbox(registry)
    await ana.put(SETTINGS, json={"config": {**SONNET, "stt": "soniox"}, "team": True})
    mine = await ana.put(SETTINGS, json={"config": {"voice": "carolina"}})
    assert mine.status_code == 200, mine.text

    config = (await ana.get(CONFIG)).json()
    assert config["voice"]["voice_id"] == CAROLINA
    assert config["llm"]["model"] == "claude-sonnet-4-5"
    assert config["stt"]["provider"] == "soniox"


async def test_clearing_your_corner_leaves_the_team_standing(
    ana: httpx.AsyncClient, registry: Registry
) -> None:
    """An empty row supplies nothing: it used to win, and blank every knob under it."""
    await in_the_sandbox(registry)
    await ana.put(SETTINGS, json={"config": {**SONNET, "stt": "soniox"}, "team": True})
    await ana.put(SETTINGS, json={"config": {"voice": "carolina"}})
    cleared = await ana.put(SETTINGS, json={"config": {}, "if_version": 1})
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["yours"]["version"] == 2

    config = (await ana.get(CONFIG)).json()
    assert config["voice"] is None
    assert (config["llm"]["model"], config["stt"]["provider"]) == ("claude-sonnet-4-5", "soniox")


# `bases` is the knob that had no way to say "none": an empty list was dropped on its way into the
# column, so a person taking the team's bases out of their own corner went on reading them.
async def test_an_empty_bases_of_your_own_wins_over_the_teams(
    ana: httpx.AsyncClient, registry: Registry
) -> None:
    await in_the_sandbox(registry)
    await ana.put(SETTINGS, json={"config": {"bases": [{"base": "clinica"}]}, "team": True})
    mine = await ana.put(SETTINGS, json={"config": {"bases": []}})
    assert mine.status_code == 200, mine.text
    assert mine.json()["yours"]["config"]["bases"] == []

    # What the worker builds the session from: no base at all, and no search tool with it.
    assert (await ana.get(CONFIG)).json()["bases"] == []


async def test_bases_nobody_attached_fall_through_to_the_teams(
    ana: httpx.AsyncClient, registry: Registry
) -> None:
    await in_the_sandbox(registry)
    await ana.put(SETTINGS, json={"config": {"bases": [{"base": "clinica"}]}, "team": True})
    await ana.put(SETTINGS, json={"config": {"voice": "carolina"}})
    assert [one["base"] for one in (await ana.get(CONFIG)).json()["bases"]] == ["clinica"]


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
    fleet_http: httpx.AsyncClient, settings: Settings
) -> None:
    answering_in(PRODUCTION, settings)
    put = await fleet_http.put(SETTINGS, json={"config": SONNET})
    assert put.status_code == 200, put.text
    assert put.json()["production"]["version"] == 1
    assert put.json()["production"]["config"]["llm"] == SONNET["llm"]


async def test_a_person_the_org_keeps_out_of_production_is_refused_there_by_name(
    ana: httpx.AsyncClient, settings: Settings
) -> None:
    answering_in(PRODUCTION, settings)
    refused = await ana.put(SETTINGS, json={"config": SONNET}, headers={ENV_HEADER: "production"})
    assert refused.status_code == 403
    assert refused.json()["detail"] == NO_PRODUCTION.format(name="Ana")


# ── words ───────────────────────────────────────────────────────────────────────


async def test_a_words_key_sets_the_opening_and_the_knowledge_and_carries_the_pipeline_over(
    ana: httpx.AsyncClient, carla: httpx.AsyncClient, registry: Registry
) -> None:
    await in_the_sandbox(registry)
    await ana.put(SETTINGS, json={"config": SONNET, "team": True})
    words = {
        "greeting": {"say": "Buenas, Clínica Norte."},
        "knowledge": "# Clínica Norte\n\nDe 9 a 20.",
    }
    put = await carla.put(SETTINGS, json={"config": words})
    assert put.status_code == 200, put.text
    team = put.json()["team"]
    assert (team["version"], team["author"]) == (2, "m_carla")
    assert team["config"]["llm"] == SONNET["llm"]
    assert team["config"]["knowledge"] == "# Clínica Norte\n\nDe 9 a 20."
    assert team["config"]["greeting"] == {
        "say": "Buenas, Clínica Norte.",
        "reply": None,
        "allow_interruptions": None,
    }
    # What the floor wrote is what the next call reads, in the static block.
    assert (await ana.get(CONFIG)).json()["knowledge"] == "# Clínica Norte\n\nDe 9 a 20."


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
    # Taking the bases out is a move of the pipeline like any other, now that it can be said.
    detached = await carla.put(SETTINGS, json={"config": {"bases": []}})
    assert detached.status_code == 403 and detached.json()["detail"].startswith("bases: ")
    # How long a voice call may run is the org's to decide, not the floor's.
    longer = await carla.put(SETTINGS, json={"config": {"max_duration_s": 3600}})
    assert longer.status_code == 403 and longer.json()["detail"].startswith("max_duration_s: ")


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


async def test_a_voice_calls_limit_is_kept_and_one_out_of_range_is_refused_by_the_shape(
    ana: httpx.AsyncClient,
) -> None:
    kept = await ana.put(SETTINGS, json={"config": {"max_duration_s": 900}})
    assert kept.status_code == 200, kept.text
    assert kept.json()["yours"]["config"]["max_duration_s"] == 900
    refused = await ana.put(SETTINGS, json={"config": {"max_duration_s": 30}})
    assert refused.status_code == 400
    assert "0 for no limit, or 60 to 3600 seconds" in refused.json()["detail"]


# A model the box does not lend the org is refused when it is PICKED, in the sentence a call would
# refuse with — never saved to go silent on the next call. The org's own key lifts it.
async def test_a_model_the_box_does_not_lend_is_refused_when_picked_and_its_own_key_lifts_it(
    ana: httpx.AsyncClient, orgs: MemoryOrgs, vault: Vault | None
) -> None:
    lends = frozenset({"deepgram", "cartesia", "anthropic/claude-haiku-4-5"})
    await orgs.set_quotas(A_RECORD.org, Quotas(lends=lends))
    refused = await ana.put(SETTINGS, json={"config": {"llm": "anthropic/claude-opus-5"}})
    assert refused.status_code == 422
    assert "anthropic/claude-opus-5 is not lent" in refused.json()["detail"]
    assert (await ana.get(SETTINGS)).json()["yours"] is None, "nothing was kept"
    cheap = await ana.put(SETTINGS, json={"config": {"llm": "anthropic/claude-haiku-4-5"}})
    assert cheap.status_code == 200, cheap.text
    assert vault is not None
    await vault.put(A_RECORD.org, "anthropic", "sk-the-orgs-own")
    theirs = await ana.put(
        SETTINGS, json={"config": {"llm": "anthropic/claude-opus-5"}, "if_version": 1}
    )
    assert theirs.status_code == 200, theirs.text

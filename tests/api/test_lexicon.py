"""The org's lexicon over the real ASGI app: the floor sets the words, every agent says them."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.auth.keys import NOT_OPENED, KeyRecord, MemoryKeys
from pinecall.types import ROLE_SCOPES, SANDBOX
from pinecall_protocol import defs
from tests.api.conftest import A_KEY, A_RECORD, AGENT, over_the_asgi_app

pytestmark = pytest.mark.unit

LEXICON = "/v1/lexicon"
CONFIG = f"/v1/agents/{AGENT}/config"

ANA_KEY = "pk_test_ana_writes_the_agent"
ANA = KeyRecord(
    key_id="k_ana", org=A_RECORD.org, env=SANDBOX, scopes=ROLE_SCOPES["developer"], subject="m_ana"
)
CARLA_KEY = "pk_test_carla_hears_the_calls"
CARLA = KeyRecord(
    key_id="k_carla",
    org=A_RECORD.org,
    env=SANDBOX,
    scopes=ROLE_SCOPES["supervisor"],
    subject="m_carla",
)
EVA_KEY = "pk_test_eva_reads"
EVA = KeyRecord(
    key_id="k_eva", org=A_RECORD.org, env=SANDBOX, scopes=ROLE_SCOPES["qa"], subject="m_eva"
)

WORDS = {"said": [{"word": "GSA", "spoken": "G S A"}], "heard": ["Maravilla"]}


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, ANA_KEY: ANA, CARLA_KEY: CARLA, EVA_KEY: EVA})


@pytest.fixture
async def carla(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = over_the_asgi_app(f"Bearer {CARLA_KEY}")
    yield http
    await http.aclose()


@pytest.fixture
async def ana(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = over_the_asgi_app(f"Bearer {ANA_KEY}")
    yield http
    await http.aclose()


@pytest.fixture
async def production(wired: None) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = over_the_asgi_app(f"Bearer {A_KEY}")
    yield http
    await http.aclose()


async def test_a_supervisor_sets_the_teams_words_and_the_next_call_says_them(
    carla: httpx.AsyncClient, ana: httpx.AsyncClient, registry: Registry
) -> None:
    await registry.register(
        "app_ci", A_RECORD.org, SANDBOX, AGENT, [defs.Route(channel="web", number=None)]
    )
    await registry.configure(
        "app_ci",
        SANDBOX,
        AGENT,
        defs.AgentConfig(says=[defs.Pronunciation(word="Vidal", spoken="bidál")], hears=["Vidal"]),
    )
    put = await carla.put(LEXICON, json={"lexicon": WORDS, "note": "said wrong all morning"})
    assert put.status_code == 200, put.text
    # A supervisor holds no agent, so their set is the team's: no corner of their own to hear it.
    assert put.json()["yours"] is None
    team = put.json()["team"]
    assert (team["version"], team["holder"], team["author"]) == (1, "", "m_carla")
    assert team["lexicon"] == WORDS
    config = (await ana.get(CONFIG)).json()
    assert config["says"] == {"Vidal": "bidál", "GSA": "G S A"}
    assert config["hears"] == ["Vidal", "Maravilla"]


async def test_a_blank_word_is_refused_in_the_shapes_own_sentence(carla: httpx.AsyncClient) -> None:
    refused = await carla.put(LEXICON, json={"lexicon": {"said": [], "heard": [" "]}})
    assert refused.status_code == 400 and "the lexicon" in refused.json()["detail"]


async def test_a_stale_version_is_told_where_the_corner_is_now(carla: httpx.AsyncClient) -> None:
    await carla.put(LEXICON, json={"lexicon": WORDS})
    await carla.put(LEXICON, json={"lexicon": WORDS, "if_version": 1})
    stale = await carla.put(LEXICON, json={"lexicon": WORDS, "if_version": 1})
    assert stale.status_code == 409 and "v2" in stale.json()["detail"]
    history = (await carla.get(f"{LEXICON}/history")).json()
    assert [row["version"] for row in history["rows"]] == [2, 1]


async def test_production_says_what_a_key_that_acts_there_set_and_the_sandbox_never_leaks(
    carla: httpx.AsyncClient, production: httpx.AsyncClient
) -> None:
    await carla.put(LEXICON, json={"lexicon": WORDS})
    assert (await production.get(LEXICON)).json()["production"] is None
    put = await production.put(LEXICON, json={"lexicon": WORDS})
    assert put.status_code == 200, put.text
    assert put.json()["production"]["lexicon"] == WORDS


async def test_a_key_that_only_reads_is_refused_and_told_both_scopes_that_open_it(
    wired: None,  # noqa: ARG001
) -> None:
    eva = over_the_asgi_app(f"Bearer {EVA_KEY}")
    try:
        refused = await eva.get(LEXICON)
    finally:
        await eva.aclose()
    assert refused.status_code == 403
    assert refused.json()["detail"] == NOT_OPENED.format(
        scope="pipeline or words", opens=" · ".join(sorted(EVA.scopes))
    )

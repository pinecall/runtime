"""POST …/settings/promote: yours to the team's, the team's to production once the goldens hold."""

from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.api.agents.registry import Registry
from pinecall.api.promoting import NO_GOLDENS, NOTHING_OF_YOURS, YOURS_DIFFERS
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.orgs.tuning import MemoryTuning
from pinecall.types import PRODUCTION, ROLE_SCOPES, SANDBOX, Tuning
from pinecall_protocol import defs
from tests.api.conftest import A_KEY, A_RECORD, over_the_asgi_app
from tests.api.evals.conftest import AGENT, a_golden
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

PROMOTE = f"/v1/agents/{AGENT}/settings/promote"

ANA_KEY = "pk_test_ana_promotes"
ANA = KeyRecord(
    key_id="k_ana", org=A_RECORD.org, env=SANDBOX, scopes=ROLE_SCOPES["developer"], subject="m_ana"
)
SONNET = Tuning(llm="anthropic/claude-sonnet-4-5")
HAIKU = Tuning(llm="anthropic/claude-haiku-4-5")
GREETS = a_golden("greets", ["hola"], expect={"says": ["Clara"]})


@pytest.fixture
def keys() -> MemoryKeys:
    return MemoryKeys({A_KEY: A_RECORD, ANA_KEY: ANA})


# suite_http is taken for its side: it wires the scripted model, the runner and the runs into the
# app, which every client of the app then shares. This one knocks with Ana's sandbox key.
@pytest.fixture
async def ana(suite_http: httpx.AsyncClient) -> AsyncIterator[httpx.AsyncClient]:  # noqa: ARG001
    http = over_the_asgi_app(f"Bearer {ANA_KEY}")
    yield http
    await http.aclose()


async def serving_in_the_sandbox(registry: Registry) -> None:
    """The clinic on air in the org's own sandbox corner, which Ana's corner falls back to."""
    await registry.register(
        "app_the_sandbox", A_RECORD.org, SANDBOX, AGENT, [defs.Route(channel="web", number=None)]
    )
    await registry.configure("app_the_sandbox", SANDBOX, AGENT, defs.AgentConfig(language="es"))


async def the_team_set(tuning: MemoryTuning, what: Tuning) -> None:
    await tuning.put(
        A_RECORD.org, SANDBOX, "", AGENT, what, author="m_ana", note=None, if_version=None
    )


async def test_a_green_run_promotes_the_teams_sandbox_to_production(
    ana: httpx.AsyncClient, registry: Registry, llm: FakeLLM, tuning: MemoryTuning
) -> None:
    await serving_in_the_sandbox(registry)
    await the_team_set(tuning, SONNET)
    llm.script.append(Scripted(chunks=("Buenos días, soy Clara.",)))
    answered = await ana.post(PROMOTE, json={"to": "production", "goldens": [GREETS]})
    assert answered.status_code == 200, answered.text
    said = answered.json()
    assert (said["world"], said["holder"], said["version"]) == ("production", "", 1)
    assert said["run"].startswith("run_")
    production = await tuning.own(A_RECORD.org, PRODUCTION, "", AGENT)
    assert production is not None and production.value == SONNET
    assert production.note == "promoted from sandbox v1"


async def test_a_golden_that_does_not_hold_keeps_production_as_it_is(
    ana: httpx.AsyncClient, registry: Registry, llm: FakeLLM, tuning: MemoryTuning
) -> None:
    await serving_in_the_sandbox(registry)
    await the_team_set(tuning, SONNET)
    llm.script.append(Scripted(chunks=("Adiós.",)))
    refused = await ana.post(PROMOTE, json={"to": "production", "goldens": [GREETS]})
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"].startswith("1 of 1 goldens did not hold")
    assert "greets" in refused.json()["detail"]
    assert await tuning.own(A_RECORD.org, PRODUCTION, "", AGENT) is None


async def test_no_goldens_promotes_nothing(
    ana: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    await serving_in_the_sandbox(registry)
    await the_team_set(tuning, SONNET)
    refused = await ana.post(PROMOTE, json={"to": "production", "goldens": []})
    assert (refused.status_code, refused.json()["detail"]) == (400, NO_GOLDENS)


async def test_a_corner_that_differs_from_the_teams_is_told_to_promote_it_first(
    ana: httpx.AsyncClient, registry: Registry, tuning: MemoryTuning
) -> None:
    await serving_in_the_sandbox(registry)
    await the_team_set(tuning, SONNET)
    await tuning.put(
        A_RECORD.org, SANDBOX, "m_ana", AGENT, HAIKU, author="m_ana", note=None, if_version=None
    )
    refused = await ana.post(PROMOTE, json={"to": "production", "goldens": [GREETS]})
    assert refused.status_code == 409
    assert refused.json()["detail"] == YOURS_DIFFERS.format(slug=AGENT, fields="llm")


async def test_yours_becomes_the_teams_next_version(
    ana: httpx.AsyncClient, tuning: MemoryTuning
) -> None:
    nothing = await ana.post(PROMOTE, json={"to": "team"})
    assert (nothing.status_code, nothing.json()["detail"]) == (
        404,
        NOTHING_OF_YOURS.format(slug=AGENT),
    )
    await tuning.put(
        A_RECORD.org, SANDBOX, "m_ana", AGENT, HAIKU, author="m_ana", note=None, if_version=None
    )
    promoted = await ana.post(PROMOTE, json={"to": "team"})
    assert promoted.status_code == 200, promoted.text
    assert promoted.json() == {"world": "sandbox", "holder": "", "version": 1, "run": None}
    team = await tuning.own(A_RECORD.org, SANDBOX, "", AGENT)
    assert team is not None and team.value == HAIKU and team.note == "promoted from m_ana v1"

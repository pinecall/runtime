"""GET /v1/personas/{name}/runs: one caller's simulations, newest first, and nobody else's."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient

from pinecall.api.app import app
from pinecall.api.evals.personas import get_personas
from pinecall.auth.keys import KeyRecord, MemoryKeys
from pinecall.log.store import MemoryStore
from pinecall.orgs.personas import MemoryPersonas
from tests.api.conftest import A_KEY, A_RECORD
from tests.api.talking import got

pytestmark = pytest.mark.unit

THE_AGENT = "clinica-norte"
ANOTHER_AGENT = "tienda-sur"
DANA = "homeowner"

# A second org with a caller of the same name: the doors are the org's, so neither sees the other.
ANOTHER_KEY = "pk_test_the_neighbours_own"
ANOTHER_ORG = KeyRecord(key_id="k_2", org="vecina", label="the neighbour")


def runs_of(persona: str = DANA, query: str = "") -> str:
    """The door, by the caller whose runs are asked for."""
    return f"/v1/personas/{persona}/runs{query}"


@pytest.fixture
def keys() -> MemoryKeys:
    """Two keys of two orgs: the clinic's, and the neighbour's."""
    return MemoryKeys({A_KEY: A_RECORD, ANOTHER_KEY: ANOTHER_ORG})


@pytest.fixture(autouse=True)
def personas() -> Iterator[MemoryPersonas]:
    """The callers both orgs wrote, before any of them has called."""
    kept = MemoryPersonas()
    app.dependency_overrides[get_personas] = lambda: kept
    yield kept
    app.dependency_overrides.pop(get_personas, None)


@pytest.fixture(autouse=True)
async def written(personas: MemoryPersonas) -> None:
    """One caller of each org, both called `homeowner`: the name is not what tells them apart."""
    for org in (A_RECORD.org, ANOTHER_ORG.org):
        await personas.put(
            org,
            DANA,
            about="",
            goal="get a move-out cleaning quote",
            style="friendly, a little rushed",
            facts={},
            state={},
            author="Ana",
        )


async def a_run(
    store: MemoryStore,
    call: str,
    *,
    persona: str | None = DANA,
    agent: str = THE_AGENT,
    org: str = A_RECORD.org,
    turns: int = 2,
    outcome: str = "quoted the move-out clean",
    judges: list[dict[str, object]] | None = None,
    ended: bool = True,
) -> None:
    """One simulated call as the two paths write it: the persona on call.started, then the turns."""
    await store.owned(call, agent, org, "production", "")
    started = {
        "channel": "web",
        "direction": "inbound",
        "from": "web_1",
        "to": agent,
        "caller": None,
        "started_at": 1.0,
        "persona": persona,
    }
    await store.append(call, agent, "call.started", started)
    for turn in range(turns):
        await store.append(call, agent, "turn.user", {"text": f"line {turn}"})
        await store.append(call, agent, "turn.agent", {"text": "of course"})
    if not ended:
        return
    await store.append(
        call, agent, "call.ended", {"reason": "caller_hung_up", "ended_by": "caller"}
    )
    await store.append(
        call,
        agent,
        "call.summary",
        {"reason": "caller_hung_up", "outcome": outcome, "duration_s": 8.0, "turns": turns},
    )
    if judges is not None:
        await store.append(call, agent, "call.score", {"judges": judges, "judge_calls": 0})


def judged(name: str, verdict: str, reason: str = "") -> dict[str, object]:
    """One judgment as call.score carries it."""
    return {"name": name, "verdict": verdict, "criteria": "", "reason": reason, "evidence": {}}


async def test_a_caller_nobody_has_called_as_has_run_nothing(
    gateway: TestClient, store: MemoryStore
) -> None:
    """A caller that exists and has never called answers an empty page, not a refusal."""
    await a_run(store, "CA_somebody_elses", persona="price-shopper")

    assert got(gateway, runs_of()) == (200, {"runs": [], "total": 0, "next": None})


async def test_only_this_callers_runs_are_listed_and_the_newest_is_first(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_run(store, "CA_older")
    await a_run(store, "CA_newer", agent=ANOTHER_AGENT)
    await a_run(store, "CA_another_caller", persona="price-shopper")
    await a_run(store, "CA_a_person", persona=None)

    status, body = got(gateway, runs_of())

    assert status == 200
    assert [row["call"] for row in body["runs"]] == ["CA_newer", "CA_older"]
    assert body["total"] == 2
    assert [row["agent"] for row in body["runs"]] == [ANOTHER_AGENT, THE_AGENT]


async def test_a_row_says_when_how_long_how_it_ended_what_it_cost_and_what_the_judges_said(
    gateway: TestClient, store: MemoryStore
) -> None:
    """Everything the pane draws, and every one of them off the call index."""
    await a_run(store, "CA_judged", turns=3, judges=[judged("consent", "held")])

    [row] = got(gateway, runs_of())[1]["runs"]

    assert row["call"] == "CA_judged"
    assert row["turns"] == 3
    assert row["started_at"] > 0
    assert row["ended_at"] is not None
    assert row["end_reason"] == "caller_hung_up"
    assert row["outcome"] == "quoted the move-out clean"
    assert row["score"] == {"held": 1, "judged": 1, "passed": True, "reason": None}


async def test_a_run_nobody_judged_says_so_rather_than_inventing_a_verdict(
    gateway: TestClient, store: MemoryStore
) -> None:
    await a_run(store, "CA_unjudged")

    [row] = got(gateway, runs_of())[1]["runs"]

    assert row["score"] is None


async def test_the_page_is_cut_below_a_cursor_and_says_when_it_is_the_last_one(
    gateway: TestClient, store: MemoryStore
) -> None:
    """The sessions list's own paging: `next` is the last call of the page, `before` continues."""
    for number in range(3):
        await a_run(store, f"CA_{number}")

    status, first = got(gateway, runs_of(query="?limit=2"))
    assert status == 200
    assert [row["call"] for row in first["runs"]] == ["CA_2", "CA_1"]
    assert (first["total"], first["next"]) == (3, "CA_1")

    _, second = got(gateway, runs_of(query="?limit=2&before=CA_1"))
    assert [row["call"] for row in second["runs"]] == ["CA_0"]
    assert (second["total"], second["next"]) == (3, None)


async def test_another_orgs_key_reads_its_own_callers_runs_and_never_these(
    gateway: TestClient, store: MemoryStore
) -> None:
    """Both orgs wrote `homeowner`; the calls of one are no part of the other's answer."""
    await a_run(store, "CA_ours")
    await a_run(store, "CA_theirs", org=ANOTHER_ORG.org, agent="vecina-sur")

    ours = got(gateway, runs_of())[1]
    theirs = got(gateway, runs_of(), bearer=ANOTHER_KEY)[1]

    assert [row["call"] for row in ours["runs"]] == ["CA_ours"]
    assert [row["call"] for row in theirs["runs"]] == ["CA_theirs"]


async def test_a_caller_this_org_never_wrote_is_a_404_and_not_an_empty_page(
    gateway: TestClient, store: MemoryStore, personas: MemoryPersonas
) -> None:
    """An empty page would read as a caller who never called; nobody wrote this one at all."""
    await personas.drop(ANOTHER_ORG.org, DANA)
    await a_run(store, "CA_ours")

    status, body = got(gateway, runs_of(), bearer=ANOTHER_KEY)

    assert status == 404
    assert DANA in body["detail"]

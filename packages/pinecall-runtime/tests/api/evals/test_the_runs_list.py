"""GET /v1/evals/runs: newest first, and never two organisations' agents in one list."""

from typing import Any

import httpx
import pytest

from pinecall.evals.run_store import EvalRun, MemoryRuns
from pinecall.log.store import MemoryStore
from tests.api.conftest import A_RECORD

pytestmark = pytest.mark.unit

RUNS = "/v1/evals/runs"

# Two agents of two different organisations, and one run each, interleaved in time so that a list
# that only ordered by `started_at` would hand the screen both.
CLINIC = "clinica-norte"
SHOP = "tienda-sur"


def a_run(id: str, agent: str, started_at: float) -> EvalRun:
    """One finished run of one agent: the row the door answers with, and nothing more."""
    return EvalRun(id=id, agent=agent, started_at=started_at, status="done", finished_at=started_at)


async def two_agents_of_runs(runs: MemoryRuns) -> None:
    """Four runs, two per agent, written oldest first so the newest of each is the later one."""
    for row in (
        a_run("run_clinic_old", CLINIC, 100.0),
        a_run("run_shop_old", SHOP, 200.0),
        a_run("run_clinic_new", CLINIC, 300.0),
        a_run("run_shop_new", SHOP, 400.0),
    ):
        await runs.put(row)


async def test_the_runs_of_one_agent_never_carry_another_agents_run(
    suite_http: httpx.AsyncClient, eval_runs: MemoryRuns
) -> None:
    """An agent belongs to one organisation: its screen sees its own runs and nobody else's."""
    await two_agents_of_runs(eval_runs)

    answered = await suite_http.get(RUNS, params={"agent": CLINIC})

    assert answered.status_code == 200, answered.text
    listed: list[dict[str, Any]] = answered.json()["runs"]
    assert [run["id"] for run in listed] == ["run_clinic_new", "run_clinic_old"]
    assert {run["agent"] for run in listed} == {CLINIC}


async def test_a_list_asked_for_no_agent_is_still_the_whole_fleets(
    suite_http: httpx.AsyncClient, eval_runs: MemoryRuns
) -> None:
    """The door's older shape: a drift check over the box reads every run there is, newest first."""
    await two_agents_of_runs(eval_runs)

    listed: list[dict[str, Any]] = (await suite_http.get(RUNS)).json()["runs"]

    assert [run["id"] for run in listed] == [
        "run_shop_new",
        "run_clinic_new",
        "run_shop_old",
        "run_clinic_old",
    ]


async def test_the_limit_of_one_agents_list_counts_that_agents_runs_alone(
    suite_http: httpx.AsyncClient, eval_runs: MemoryRuns
) -> None:
    """The point of the clause: a busy org never pushes this agent's older run off the page."""
    await two_agents_of_runs(eval_runs)

    listed: list[dict[str, Any]] = (
        await suite_http.get(RUNS, params={"agent": CLINIC, "limit": 2})
    ).json()["runs"]

    assert [run["id"] for run in listed] == ["run_clinic_new", "run_clinic_old"]


async def test_since_and_agent_narrow_the_same_list_together(
    suite_http: httpx.AsyncClient, eval_runs: MemoryRuns
) -> None:
    """A drift check reads one agent's runs after the one it is comparing to, and only those."""
    await two_agents_of_runs(eval_runs)

    listed: list[dict[str, Any]] = (
        await suite_http.get(RUNS, params={"agent": CLINIC, "since": 150.0})
    ).json()["runs"]

    assert [run["id"] for run in listed] == ["run_clinic_new"]


# The cut used to come BEFORE the org filter: `newest(limit)` took the box's newest runs whatever
# org they belong to, and what survived the filter was whatever share of them happened to be this
# key's. On a box where another tenant ran last, `?limit=2` answered an empty list — found against
# production with `pinecall runs list --limit 2` (2026-09-20).
async def test_the_limit_counts_this_orgs_runs_and_not_the_boxs(
    suite_http: httpx.AsyncClient, eval_runs: MemoryRuns, store: MemoryStore
) -> None:
    """Two tenants on one box, the other one's runs newest: the page is still mine and full."""
    await store.owned(None, CLINIC, A_RECORD.org)
    await store.owned(None, SHOP, "tienda")
    await two_agents_of_runs(eval_runs)

    listed: list[dict[str, Any]] = (await suite_http.get(RUNS, params={"limit": 2})).json()["runs"]

    assert [run["id"] for run in listed] == ["run_clinic_new", "run_clinic_old"]

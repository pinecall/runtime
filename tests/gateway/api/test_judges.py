"""Tests for the judge doors: an agent's own questions for its calls."""

import httpx
import pytest

from pinecall.domain.names import JsonObject
from tests.conftest import Knocking, issued, postgres

JUDGES = "/v1/agents/clinica-norte/judges"


SLOT: JsonObject = {"question": "The agent offered the next free slot."}


async def written(knocking: Knocking, name: str, body: JsonObject = SLOT) -> httpx.Response:
    async with knocking.http(knocking.app["sandbox"]) as http:
        answer = await http.put(f"{JUDGES}/{name}", json=body)
    assert answer.status_code == 200, answer.text
    return answer


@postgres
async def test_an_agent_nobody_wrote_a_judge_for_lists_none(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        listed = await http.get(JUDGES)
    assert (listed.status_code, listed.json()) == (200, {"judges": []})


@postgres
async def test_one_written_comes_back_with_every_call_and_who_wrote_it(
    knocking: Knocking,
) -> None:
    [row] = (await written(knocking, "offers-next-slot")).json()["judges"]
    assert (row["name"], row["question"]) == ("offers-next-slot", SLOT["question"])
    assert row["runs_on"] == "every-call"
    assert row["author"] != ""
    assert row["set_at"] > 0


@postgres
async def test_writing_the_same_name_again_replaces_it(knocking: Knocking) -> None:
    await written(knocking, "offers-next-slot")
    again = await written(
        knocking, "offers-next-slot", {"question": "Two slots.", "runs_on": "simulations"}
    )
    assert [(row["question"], row["runs_on"]) for row in again.json()["judges"]] == [
        ("Two slots.", "simulations")
    ]


@postgres
async def test_when_it_runs_is_every_call_or_simulations_and_nothing_else(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        refused = await http.put(
            f"{JUDGES}/offers-next-slot", json={**SLOT, "runs_on": "sometimes"}
        )
    assert refused.status_code == 422


# A name is what call.score names the verdict with.
@postgres
@pytest.mark.parametrize("name", ["Next%20Slot", "next_slot", "next--slot", "-next"])
async def test_a_name_that_is_not_a_name_is_refused(knocking: Knocking, name: str) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        refused = await http.put(f"{JUDGES}/{name}", json=SLOT)
    assert refused.status_code == 400
    assert "lower-case words joined by hyphens" in refused.json()["detail"]


@postgres
async def test_one_dropped_is_gone_and_a_name_nobody_wrote_is_a_404(knocking: Knocking) -> None:
    await written(knocking, "offers-next-slot")
    async with knocking.http(knocking.app["sandbox"]) as http:
        dropped = await http.delete(f"{JUDGES}/offers-next-slot")
        again = await http.delete(f"{JUDGES}/offers-next-slot")
    assert dropped.json() == {"judges": []}
    assert again.status_code == 404


@postgres
async def test_a_judge_is_one_agents_and_not_another_agents(knocking: Knocking) -> None:
    await written(knocking, "offers-next-slot")
    async with knocking.http(knocking.app["sandbox"]) as http:
        listed = await http.get("/v1/agents/tienda-sur/judges")
    assert listed.json() == {"judges": []}


@postgres
async def test_one_judge_written_once_is_the_agents_in_both_worlds(knocking: Knocking) -> None:
    await written(knocking, "offers-next-slot")
    async with knocking.http(knocking.app["production"]) as http:
        listed = await http.get(JUDGES)
    assert [row["name"] for row in listed.json()["judges"]] == ["offers-next-slot"]


@postgres
async def test_a_key_without_evals_is_refused(knocking: Knocking) -> None:
    reader = await issued(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", frozenset({"calls"})
    )
    async with knocking.http(reader) as http:
        listed = await http.get(JUDGES)
        put = await http.put(f"{JUDGES}/offers-next-slot", json=SLOT)
        dropped = await http.delete(f"{JUDGES}/offers-next-slot")
    assert (listed.status_code, put.status_code, dropped.status_code) == (403, 403, 403)

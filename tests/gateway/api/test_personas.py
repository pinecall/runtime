"""Tests for the persona doors: the org's callers, whom they may call, and what they ran."""

import httpx
import pytest

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log.store import Claim
from tests.conftest import Knocking, issued, postgres

PERSONAS = "/v1/personas"


DANA: JsonObject = {
    "about": "A homeowner moving out.",
    "goal": "get a move-out cleaning quote",
    "style": "friendly, a little rushed",
    "facts": {"their name": "Dana Ruiz"},
}


async def written(knocking: Knocking, name: str, body: JsonObject = DANA) -> httpx.Response:
    async with knocking.http(knocking.app["sandbox"]) as http:
        answer = await http.put(f"{PERSONAS}/{name}", json=body)
    assert answer.status_code == 200, answer.text
    return answer


@postgres
async def test_an_org_with_nobody_written_for_it_lists_none(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        listed = await http.get(PERSONAS)
    assert (listed.status_code, listed.json()) == (200, {"personas": []})


@postgres
async def test_one_written_comes_back_whole_with_who_wrote_it(knocking: Knocking) -> None:
    [row] = (await written(knocking, "homeowner")).json()["personas"]
    assert (row["name"], row["goal"], row["facts"]) == ("homeowner", DANA["goal"], DANA["facts"])
    assert row["state"] == {}
    assert row["author"] != ""
    assert row["set_at"] > 0
    assert row["agents"] == []


@postgres
async def test_writing_the_same_name_again_replaces_it(knocking: Knocking) -> None:
    await written(knocking, "homeowner")
    again = await written(knocking, "homeowner", {**DANA, "goal": "get a price today"})
    assert [row["goal"] for row in again.json()["personas"]] == ["get a price today"]


@postgres
async def test_a_state_travels_whole_for_a_caller_the_business_knows(knocking: Knocking) -> None:
    state: JsonObject = {"stage": "book", "patient": {"id": "p-1"}}
    [row] = (await written(knocking, "known", {**DANA, "state": state})).json()["personas"]
    assert row["state"] == state


@postgres
async def test_a_rename_takes_the_old_row_with_it(knocking: Knocking) -> None:
    await written(knocking, "homeowner")
    renamed = await written(knocking, "dana", {**DANA, "was": "homeowner"})
    assert [row["name"] for row in renamed.json()["personas"]] == ["dana"]


@postgres
async def test_a_rename_onto_a_name_somebody_holds_is_refused(knocking: Knocking) -> None:
    await written(knocking, "homeowner")
    await written(knocking, "dana")
    async with knocking.http(knocking.app["sandbox"]) as http:
        clash = await http.put(f"{PERSONAS}/dana", json={**DANA, "was": "homeowner"})
    assert clash.status_code == 409
    assert "already" in clash.json()["detail"]


@postgres
async def test_a_rename_of_a_caller_nobody_wrote_is_a_404(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        nobody = await http.put(f"{PERSONAS}/dana", json={**DANA, "was": "nobody"})
    assert nobody.status_code == 404


# A name is what `--persona` takes: spaces, capitals and stray hyphens are refused.
@postgres
@pytest.mark.parametrize("name", ["Dana%20Ruiz", "dana_ruiz", "dana--ruiz", "-dana"])
async def test_a_name_that_is_not_a_name_is_refused(knocking: Knocking, name: str) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        refused = await http.put(f"{PERSONAS}/{name}", json=DANA)
    assert refused.status_code == 400
    assert "lower-case words joined by hyphens" in refused.json()["detail"]


@postgres
async def test_a_caller_that_says_how_it_is_played_and_whom_it_calls_comes_back_saying_it(
    knocking: Knocking,
) -> None:
    played: JsonObject = {
        "llm": "acme/acme-2",
        "tts": "acme",
        "voice": "carolina",
        "accepts_when": "they gave a price for Friday",
        "declines_when": "they asked to be called back",
        "agents": ["clinica-norte"],
    }
    [row] = (await written(knocking, "homeowner", {**DANA, **played})).json()["personas"]
    assert {field: row[field] for field in played} == played


@postgres
async def test_a_caller_that_says_nothing_about_it_is_played_as_every_caller_is(
    knocking: Knocking,
) -> None:
    [row] = (await written(knocking, "homeowner")).json()["personas"]
    assert (row["llm"], row["tts"], row["voice"]) == (None, None, None)
    assert (row["accepts_when"], row["declines_when"]) == ("", "")


# Refused when written, not by the vendor in the middle of a run.
@postgres
@pytest.mark.parametrize(
    "knob", [{"llm": "openai-but-misspelt/gpt-5"}, {"tts": "elevenlabz/eleven_v3"}]
)
async def test_a_vendor_this_box_does_not_have_is_refused_when_it_is_written(
    knocking: Knocking, knob: JsonObject
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        refused = await http.put(f"{PERSONAS}/homeowner", json={**DANA, **knob})
        listed = await http.get(PERSONAS)
    assert refused.status_code == 400
    assert "no vendor named" in refused.json()["detail"]
    assert listed.json() == {"personas": []}


@postgres
async def test_one_dropped_is_gone_and_a_name_nobody_wrote_is_a_404(knocking: Knocking) -> None:
    await written(knocking, "homeowner")
    async with knocking.http(knocking.app["sandbox"]) as http:
        dropped = await http.delete(f"{PERSONAS}/homeowner")
        again = await http.delete(f"{PERSONAS}/homeowner")
    assert dropped.json() == {"personas": []}
    assert again.status_code == 404


# Per org, not per world: the same callers in the sandbox and in production.
@postgres
async def test_one_caller_written_once_is_the_whole_orgs_in_both_worlds(knocking: Knocking) -> None:
    await written(knocking, "homeowner")
    async with knocking.http(knocking.app["production"]) as http:
        listed = await http.get(PERSONAS)
    assert [row["name"] for row in listed.json()["personas"]] == ["homeowner"]


@postgres
async def test_an_agent_is_shown_the_callers_written_for_it_and_for_every_agent(
    knocking: Knocking,
) -> None:
    await written(knocking, "todos")
    await written(knocking, "suyo", {**DANA, "agents": ["clinica-norte"]})
    await written(knocking, "ajeno", {**DANA, "agents": ["tienda-sur"]})
    async with knocking.http(knocking.app["sandbox"]) as http:
        mine = await http.get(PERSONAS, params={"agent": "clinica-norte"})
        every = await http.get(PERSONAS)
    assert [row["name"] for row in mine.json()["personas"]] == ["suyo", "todos"]
    assert len(every.json()["personas"]) == 3


@postgres
async def test_a_key_without_evals_is_refused(knocking: Knocking) -> None:
    reader = await issued(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", frozenset({"calls"})
    )
    async with knocking.http(reader) as http:
        refused = await http.get(PERSONAS)
    assert refused.status_code == 403


# ── what a caller ran ──


async def a_run(
    knocking: Knocking,
    call: str,
    *,
    persona: str | None = "homeowner",
    turns: int = 2,
    judged: bool = False,
) -> None:
    store = knocking.gateway.logs.store
    scope = Scope(knocking.org.id, "sandbox")
    await store.claim(call, "clinica-norte", scope.org, Claim(scope))
    log = knocking.gateway.logs.writing(call, "clinica-norte")
    started: JsonObject = {
        "channel": "web",
        "direction": "inbound",
        "from": "web_1",
        "to": "clinica-norte",
        "caller": None,
        "started_at": 1.0,
        "persona": persona,
    }
    await log.append("call.started", started)
    for turn in range(turns):
        user: JsonObject = {"speech_id": f"sp_{turn}", "text": f"line {turn}", "metrics": {}}
        await log.append("turn.user", user)
    await log.append(
        "call.ended",
        {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 9.0, "duration_s": 8.0},
    )
    if judged:
        verdict: JsonObject = {
            "name": "consent",
            "verdict": "held",
            "criteria": "c",
            "reason": "r",
            "evidence": {"seqs": []},
        }
        score: JsonObject = {"judges": [verdict], "judge_calls": 0, "passed": True}
        await log.append("call.score", score)


async def runs_of(knocking: Knocking, query: str = "") -> httpx.Response:
    async with knocking.http(knocking.app["sandbox"]) as http:
        return await http.get(f"{PERSONAS}/homeowner/runs{query}")


@postgres
async def test_a_caller_nobody_has_called_as_has_run_nothing(knocking: Knocking) -> None:
    await written(knocking, "homeowner")
    await a_run(knocking, "CA_somebody_elses", persona="price-shopper")
    answer = await runs_of(knocking)
    assert (answer.status_code, answer.json()) == (200, {"runs": [], "total": 0, "next": None})


@postgres
async def test_only_this_callers_runs_are_listed_and_the_newest_is_first(
    knocking: Knocking,
) -> None:
    await written(knocking, "homeowner")
    await a_run(knocking, "CA_older")
    await a_run(knocking, "CA_newer")
    await a_run(knocking, "CA_another_caller", persona="price-shopper")
    await a_run(knocking, "CA_a_person", persona=None)
    answer = await runs_of(knocking)
    body = answer.json()
    assert answer.status_code == 200
    assert [row["call"] for row in body["runs"]] == ["CA_newer", "CA_older"]
    assert body["total"] == 2


@postgres
async def test_a_row_says_how_long_how_it_ended_and_what_the_judges_said(
    knocking: Knocking,
) -> None:
    await written(knocking, "homeowner")
    await a_run(knocking, "CA_judged", turns=3, judged=True)
    [row] = (await runs_of(knocking)).json()["runs"]
    assert (row["call"], row["agent"], row["turns"]) == ("CA_judged", "clinica-norte", 3)
    assert row["ended_at"] is not None
    assert row["end_reason"] == "caller_hung_up"
    assert row["score"] == {"held": 1, "judged": 1, "passed": True, "reason": None}


@postgres
async def test_a_run_nobody_judged_says_so_rather_than_inventing_a_verdict(
    knocking: Knocking,
) -> None:
    await written(knocking, "homeowner")
    await a_run(knocking, "CA_unjudged")
    [row] = (await runs_of(knocking)).json()["runs"]
    assert row["score"] is None


@postgres
async def test_the_page_is_cut_below_a_cursor_and_says_when_it_is_the_last_one(
    knocking: Knocking,
) -> None:
    await written(knocking, "homeowner")
    for number in range(3):
        await a_run(knocking, f"CA_{number}")
    first = (await runs_of(knocking, "?limit=2")).json()
    assert [row["call"] for row in first["runs"]] == ["CA_2", "CA_1"]
    assert (first["total"], first["next"]) == (3, "CA_1")
    second = (await runs_of(knocking, "?limit=2&before=CA_1")).json()
    assert [row["call"] for row in second["runs"]] == ["CA_0"]
    assert second["next"] is None


@postgres
async def test_a_caller_this_org_never_wrote_is_a_404_and_not_an_empty_page(
    knocking: Knocking,
) -> None:
    await a_run(knocking, "CA_ours")
    answer = await runs_of(knocking)
    assert answer.status_code == 404
    assert "homeowner" in answer.json()["detail"]

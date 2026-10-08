"""Tests for the dataset doors: a production call kept as a case, played again in the sandbox."""

from pinecall.domain.names import Env, JsonObject
from pinecall.domain.scope import Scope
from pinecall.log.store import Claim
from pinecall.providers import catalog
from tests.conftest import AGENT, Knocking, configured, postgres
from tests.gateway.api.conftest import an_app

CASES = "/v1/evals/cases"

RUN = "/v1/evals/run"

ENDED: JsonObject = {
    "reason": "caller_hung_up",
    "ended_by": "caller",
    "ended_at": 9.0,
    "duration_s": 8.0,
}


# What the panel said of a call that promised a call back nobody booked.
BROKE_A_PROMISE: JsonObject = {
    "passed": False,
    "judges": [
        {
            "name": "promises",
            "verdict": "broken",
            "criteria": "Every commitment the agent made is recorded by a tool call.",
            "reason": "it promised a call back and no tool booked one",
            "evidence": {"seqs": []},
        }
    ],
    "judge_calls": 1,
}


async def a_real_call(
    knocking: Knocking,
    *lines: str,
    env: Env = "production",
    over: bool = True,
    score: JsonObject | None = None,
) -> str:
    """A call of the org's agent in the world, its caller's lines said, ended unless not."""
    call = f"CA_{env[:4]}_{len(lines)}_{knocking.org.id[-6:]}"
    scope = Scope(knocking.org.id, env)
    await knocking.gateway.logs.store.claim(call, AGENT, scope.org, Claim(scope))
    log = knocking.gateway.logs.writing(call, AGENT)
    await log.append("state.changed", {"state": {"stage": "book"}, "changed": ["stage"]})
    for number, line in enumerate(lines):
        await log.append("turn.user", {"speech_id": f"s{number}", "text": line, "metrics": {}})
        reply: JsonObject = {
            "speech_id": f"s{number}",
            "text": "Claro.",
            "interrupted": False,
            "metrics": {},
        }
        await log.append("turn.agent", reply)
    if over:
        await log.append("call.ended", ENDED)
    if score is not None:
        await log.append("call.score", score)
    return call


@postgres
async def test_a_production_call_is_kept_as_a_case_of_the_orgs_dataset(knocking: Knocking) -> None:
    call = await a_real_call(knocking, "Quiero cita el jueves", "Por la tarde")
    going = await a_real_call(knocking, "Hola", over=False)
    async with knocking.http(knocking.app["production"]) as org:
        kept = await org.post(
            CASES,
            json={"call": call, "name": "jueves-tarde", "expect": {"says_any": ["jueves"]}},
        )
        again = await org.post(CASES, json={"call": call, "name": "jueves-tarde"})
        still = await org.post(CASES, json={"call": going, "name": "going"})
    async with knocking.http(knocking.app["sandbox"]) as org:
        listed = await org.get(CASES, params={"agent": AGENT})
        unseen = await org.post(CASES, json={"call": call, "name": "from-the-sandbox"})
    assert kept.status_code == 200, kept.text
    case = kept.json()
    golden = case["golden"]
    assert golden["input"] == ["Quiero cita el jueves", "Por la tarde"]
    assert golden["state"] == {"stage": "book"}
    assert golden["promoted_from"] == call
    assert golden["expect"] == {"says_any": ["jueves"]}
    assert (case["source_env"], case["held_out"], case["agent"]) == ("production", False, AGENT)
    assert again.status_code == 409
    assert still.status_code == 409
    assert [row["name"] for row in listed.json()["cases"]] == ["jueves-tarde"], "the org's, both"
    assert unseen.status_code == 404, "a sandbox key does not read a production call"


@postgres
async def test_a_nightly_run_plays_the_cases_not_held_out_and_a_release_names_the_rest(
    knocking: Knocking,
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured([["Claro, el jueves."]]))
    call = await a_real_call(knocking, "Quiero cita el jueves")
    async with knocking.http(knocking.app["production"]) as org:
        await org.post(CASES, json={"call": call, "name": "nightly"})
        await org.post(CASES, json={"call": call, "name": "release", "held_out": True})
    app = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as org:
        nightly = await org.post(RUN, json={"agent": AGENT, "dataset": True}, timeout=60)
        release = await org.post(
            RUN, json={"agent": AGENT, "dataset": True, "cases": ["release"]}, timeout=60
        )
        unknown = await org.post(RUN, json={"agent": AGENT, "cases": ["nobody"]})
        spoken = await org.post(RUN, json={"agent": AGENT, "dataset": True, "voice": True})
        no_version = await org.post(RUN, json={"agent": AGENT, "dataset": True, "version": 7})
    assert nightly.status_code == 200, nightly.text
    assert nightly.json()["matrix"]["goldens"] == ["nightly"]
    assert release.json()["matrix"]["goldens"] == ["nightly", "release"]
    assert unknown.status_code == 404
    assert "no case named nobody" in unknown.json()["detail"]
    assert spoken.status_code == 400
    assert no_version.status_code == 404
    await app.close()


@postgres
async def test_cases_are_never_played_through_the_live_app(knocking: Knocking) -> None:
    call = await a_real_call(knocking, "Quiero cita el jueves")
    live = await an_app(knocking, env="production")
    async with knocking.http(knocking.app["production"]) as org:
        await org.post(CASES, json={"call": call, "name": "nightly"})
        refused = await org.post(RUN, json={"agent": AGENT, "dataset": True})
    assert refused.status_code == 403
    assert "never through production's" in refused.json()["detail"]
    await live.close()


@postgres
async def test_a_case_is_forgotten_once_and_another_orgs_is_nobodys(knocking: Knocking) -> None:
    call = await a_real_call(knocking, "Hola")
    async with knocking.http(knocking.app["production"]) as org:
        case = (await org.post(CASES, json={"call": call, "name": "hola"})).json()
        gone = await org.delete(f"{CASES}/{case['id']}")
        twice = await org.delete(f"{CASES}/{case['id']}")
        listed = await org.get(CASES)
    assert (gone.status_code, twice.status_code) == (204, 404)
    assert listed.json() == {"cases": []}


@postgres
async def test_a_call_read_as_a_golden_says_what_its_broken_verdicts_forbid(
    knocking: Knocking,
) -> None:
    call = await a_real_call(knocking, "¿Me llaman mañana?", score=BROKE_A_PROMISE)
    async with knocking.http(knocking.app["production"]) as org:
        read = await org.get(f"/v1/calls/{call}/golden", params={"name": "llamada"})
        cut = await org.get(f"/v1/calls/{call}/golden", params={"from_seq": 1})
        kept = await org.post(CASES, json={"call": call, "name": "llamada"})
        cases = await org.get(CASES, params={"agent": AGENT})
    assert read.status_code == 200, read.text
    assert read.json()["expect"] == {"judges": ["promises"]}
    assert read.json()["name"] == "llamada"
    assert (cut.json()["state"], cut.json()["input"]) == ({"stage": "book"}, ["¿Me llaman mañana?"])
    case = kept.json()
    assert case["golden"]["expect"] == {"judges": ["promises"]}, "an expect left empty is derived"
    assert case["status"] == "approved", "a case a person kept is approved from the start"
    assert case["broke"] == [
        {"judge": "promises", "reason": "it promised a call back and no tool booked one"}
    ]
    assert cases.json()["pending"] == 0
    assert cases.json()["pending_at_most"] == 50


@postgres
async def test_a_judge_called_wrong_dismisses_the_case_and_labels_the_call(
    knocking: Knocking,
) -> None:
    call = await a_real_call(knocking, "¿Me llaman mañana?", score=BROKE_A_PROMISE)
    async with knocking.http(knocking.app["production"]) as org:
        case = (await org.post(CASES, json={"call": call, "name": "llamada"})).json()
        refused = await org.patch(
            f"{CASES}/{case['id']}", json={"status": "dismissed", "judge_was_wrong": "grounded"}
        )
        dismissed = await org.patch(
            f"{CASES}/{case['id']}",
            json={"status": "dismissed", "judge_was_wrong": "promises", "note": "it did book"},
        )
        labelled = await org.get("/v1/evals/calibration", params={"agent": AGENT})
        gone = await org.patch(f"{CASES}/case_nobody", json={"status": "approved"})
    assert refused.status_code == 400
    assert "did not break on grounded" in refused.json()["detail"]
    assert dismissed.status_code == 200, dismissed.text
    assert dismissed.json()["status"] == "dismissed"
    judges = {row["judge"]: row["labelled"] for row in labelled.json()["judges"]}
    assert judges == {"promises": 1}
    assert gone.status_code == 404


@postgres
async def test_the_inbox_lists_one_status_and_counts_what_waits(knocking: Knocking) -> None:
    call = await a_real_call(knocking, "Hola", score=BROKE_A_PROMISE)
    async with knocking.http(knocking.app["production"]) as org:
        await org.post(CASES, json={"call": call, "name": "hola"})
        approved = await org.get(CASES, params={"status": "approved"})
        pending = await org.get(CASES, params={"status": "pending"})
        nonsense = await org.get(CASES, params={"status": "maybe"})
    assert [row["name"] for row in approved.json()["cases"]] == ["hola"]
    assert pending.json()["cases"] == []
    assert nonsense.status_code == 422

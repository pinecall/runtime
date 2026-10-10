"""Tests for the judge doors: Pinecall's switched, the org's own, one agent's own, and a try."""

import httpx
import pytest

from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.evals.catalog import library
from pinecall.gateway.ending.seal import NO_JUDGE
from pinecall.log.store import Claim
from pinecall.providers import catalog
from pinecall.wire.rest.evals import JudgeTried
from tests.conftest import AGENT, Knocking, issued, postgres
from tests.gateway.conftest import a_hold, broken, judging

JUDGES = f"/v1/agents/{AGENT}/judges"


SLOT: JsonObject = {"question": "The agent offered the next free slot."}


LIBRARY = list(library())


async def written(
    knocking: Knocking, name: str, body: JsonObject = SLOT, where: str = JUDGES
) -> httpx.Response:
    """The list after one PUT that must succeed."""
    async with knocking.http(knocking.app["sandbox"]) as http:
        answer = await http.put(f"{where}/{name}", json=body)
    assert answer.status_code == 200, answer.text
    return answer


def own_of(answer: httpx.Response) -> list[JsonObject]:
    """The rows of a list that are not Pinecall's."""
    rows: list[JsonObject] = answer.json()["judges"]
    return [row for row in rows if row["owner"] != "pinecall"]


def switched_of(answer: httpx.Response) -> dict[str, bool]:
    """Pinecall's rows of a list: each one's name and whether it runs."""
    rows: list[JsonObject] = answer.json()["judges"]
    return {str(row["name"]): row["on"] is True for row in rows if row["owner"] == "pinecall"}


# ── the list ──


@postgres
async def test_an_agent_nobody_wrote_for_lists_pinecalls_judges_each_as_its_default(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        listed = await http.get(JUDGES)
    assert listed.status_code == 200
    rows: list[JsonObject] = listed.json()["judges"]
    assert [row["name"] for row in rows] == LIBRARY
    assert {str(row["owner"]) for row in rows} == {"pinecall"}
    assert switched_of(listed) == {name: judge.on_by_default for name, judge in library().items()}
    consent = rows[LIBRARY.index("consent")]
    assert (consent["answer"], consent["reads"], consent["version"]) == ("verdict", ["facts"], 1)
    assert consent["summary"]
    assert consent["author"] is None


@postgres
async def test_one_written_comes_back_after_pinecalls_whole_and_with_who_wrote_it(
    knocking: Knocking,
) -> None:
    [row] = own_of(await written(knocking, "offers-next-slot"))
    assert (row["name"], row["question"], row["owner"], row["on"]) == (
        "offers-next-slot",
        SLOT["question"],
        AGENT,
        True,
    )
    assert (row["answer"], row["when"], row["reads"], row["choices"]) == (
        "verdict",
        "always",
        [],
        [],
    )
    assert row["author"] != ""
    assert isinstance(row["set_at"], float)


@postgres
async def test_a_judge_that_picks_on_a_trigger_reading_the_prompt_comes_back_so(
    knocking: Knocking,
) -> None:
    body: JsonObject = {
        "question": "Why did the caller call?",
        "answer": "choice",
        "choices": ["book", "cancel", "other"],
        "when": "trigger",
        "trigger": "The caller asked for something.",
        "reads": ["prompt"],
    }
    [row] = own_of(await written(knocking, "why-they-called", body))
    assert {key: row[key] for key in body} == body


@postgres
async def test_writing_the_same_name_again_replaces_it(knocking: Knocking) -> None:
    await written(knocking, "offers-next-slot")
    again = await written(
        knocking, "offers-next-slot", {"question": "Two slots.", "when": "simulations"}
    )
    assert [(row["question"], row["when"]) for row in own_of(again)] == [
        ("Two slots.", "simulations")
    ]


@postgres
@pytest.mark.parametrize(
    "body",
    [
        {**SLOT, "when": "sometimes"},
        {**SLOT, "answer": "essay"},
        {**SLOT, "reads": ["the-mind"]},
    ],
)
async def test_a_field_out_of_its_values_is_refused(knocking: Knocking, body: JsonObject) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        refused = await http.put(f"{JUDGES}/offers-next-slot", json=body)
    assert refused.status_code == 422


@postgres
@pytest.mark.parametrize(
    "body",
    [
        {"question": " "},
        {**SLOT, "answer": "choice", "choices": ["only-one"]},
        {**SLOT, "choices": ["a", "b"]},
        {**SLOT, "when": "trigger"},
    ],
)
async def test_a_judge_that_cannot_be_asked_is_refused(
    knocking: Knocking, body: JsonObject
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        refused = await http.put(f"{JUDGES}/offers-next-slot", json=body)
    assert refused.status_code == 400, refused.text


# A name is what call.score names the answer with.
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
    assert own_of(dropped) == []
    assert again.status_code == 404


@postgres
async def test_a_judge_is_one_agents_and_not_another_agents(knocking: Knocking) -> None:
    await written(knocking, "offers-next-slot")
    async with knocking.http(knocking.app["sandbox"]) as http:
        listed = await http.get("/v1/agents/tienda-sur/judges")
    assert own_of(listed) == []


@postgres
async def test_one_judge_written_once_is_the_agents_in_both_worlds(knocking: Knocking) -> None:
    await written(knocking, "offers-next-slot")
    async with knocking.http(knocking.app["production"]) as http:
        listed = await http.get(JUDGES)
    assert [row["name"] for row in own_of(listed)] == ["offers-next-slot"]


@postgres
async def test_a_key_without_evals_is_refused(knocking: Knocking) -> None:
    reader = await issued(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", frozenset({"calls"})
    )
    async with knocking.http(reader) as http:
        listed = await http.get(JUDGES)
        put = await http.put(f"{JUDGES}/offers-next-slot", json=SLOT)
        dropped = await http.delete(f"{JUDGES}/offers-next-slot")
        tried = await http.post(f"{JUDGES}/try", json={"name": "grounded", "last": 1})
    assert [answer.status_code for answer in (listed, put, dropped, tried)] == [403] * 4


@postgres
async def test_the_orgs_judges_are_listed_on_every_agent_and_the_org_lists_its_own_alone(
    knocking: Knocking,
) -> None:
    general = await written(knocking, "never-medical-advice", where="/v1/org/judges")
    await written(knocking, "offers-next-slot")
    async with knocking.http(knocking.app["sandbox"]) as http:
        mine = await http.get(JUDGES)
        dropped = await http.delete("/v1/org/judges/never-medical-advice")
        gone = await http.delete("/v1/org/judges/never-medical-advice")
    assert [(row["name"], row["owner"]) for row in own_of(general)] == [
        ("never-medical-advice", "org")
    ]
    assert [(row["name"], row["owner"]) for row in own_of(mine)] == [
        ("never-medical-advice", "org"),
        ("offers-next-slot", AGENT),
    ]
    assert (dropped.status_code, own_of(dropped)) == (200, [])
    assert (gone.status_code, gone.json()["detail"]) == (
        404,
        "the org has no judge called never-medical-advice",
    )


@postgres
async def test_a_name_the_org_took_is_refused_to_an_agent(knocking: Knocking) -> None:
    await written(knocking, "offers-next-slot", where="/v1/org/judges")
    async with knocking.http(knocking.app["sandbox"]) as http:
        shared = await http.put(f"{JUDGES}/offers-next-slot", json=SLOT)
    assert shared.status_code == 409
    assert shared.json()["detail"] == (
        "offers-next-slot is the org judge already: one name asks one question of a call"
    )


# ── Pinecall's judges switched ──


@postgres
async def test_one_of_pinecalls_is_switched_by_the_org_and_the_agents_switch_wins(
    knocking: Knocking,
) -> None:
    org_wide = await written(knocking, "sentiment", {"on": True}, "/v1/org/judges")
    assert switched_of(org_wide)["sentiment"] is True
    async with knocking.http(knocking.app["sandbox"]) as http:
        inherited = await http.get(JUDGES)
    assert switched_of(inherited)["sentiment"] is True
    off_here = await written(knocking, "sentiment", {"on": False})
    assert switched_of(off_here)["sentiment"] is False
    async with knocking.http(knocking.app["sandbox"]) as http:
        elsewhere = await http.get("/v1/agents/tienda-sur/judges")
    assert switched_of(elsewhere)["sentiment"] is True


@postgres
async def test_one_of_pinecalls_takes_only_on_and_is_never_deleted(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        questioned = await http.put(f"{JUDGES}/consent", json={**SLOT, "on": False})
        nothing = await http.put(f"{JUDGES}/consent", json={})
        dropped = await http.delete(f"{JUDGES}/consent")
        org_dropped = await http.delete("/v1/org/judges/consent")
    refusals = (questioned, nothing, dropped, org_dropped)
    assert [answer.status_code for answer in refusals] == [409] * 4
    assert "only whether it runs is written" in questioned.json()["detail"]
    assert '{"on": false}' in dropped.json()["detail"]


@postgres
async def test_a_judge_of_ones_own_takes_no_switch(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        refused = await http.put(f"{JUDGES}/offers-next-slot", json={**SLOT, "on": False})
    assert refused.status_code == 409
    assert "DELETE stops it" in refused.json()["detail"]


# ── a try ──


GREETED: tuple[tuple[str, JsonObject], ...] = (
    ("turn.user", {"speech_id": "sp_1", "text": "Hola", "metrics": {}}),
    ("turn.agent", {"speech_id": "sp_1", "text": "Buenas.", "interrupted": False, "metrics": {}}),
)


async def a_finished_call(knocking: Knocking, call: str, *, over: bool = True) -> str:
    """A sandbox call of the agent that said hello, ended unless told otherwise."""
    scope = Scope(knocking.org.id, "sandbox")
    await knocking.gateway.logs.store.claim(call, AGENT, scope.org, Claim(scope))
    log = knocking.gateway.logs.writing(call, AGENT)
    for kind, data in GREETED:
        await log.append(kind, data)
    if over:
        ended: JsonObject = {
            "reason": "caller_hung_up",
            "ended_by": "caller",
            "ended_at": 9.0,
            "duration_s": 8.0,
        }
        await log.append("call.ended", ended)
    return call


async def tried(knocking: Knocking, body: JsonObject) -> httpx.Response:
    """The answer of one try."""
    async with knocking.http(knocking.app["sandbox"]) as http:
        return await http.post(f"{JUDGES}/try", json=body)


def tried_of(answer: httpx.Response) -> JudgeTried:
    """A try that succeeded, read."""
    assert answer.status_code == 200, answer.text
    return JudgeTried.model_validate(answer.json())


def verdicts_of(answer: JudgeTried) -> list[tuple[str, str | None, str | None]]:
    """Each call tried: the call, the judge that answered, and its verdict."""
    return [
        (
            row.call,
            None if row.judgment is None else row.judgment.name,
            None if row.judgment is None else row.judgment.verdict,
        )
        for row in answer.rows
    ]


# Each call asks a model of its own, so the scripted model answers each the same.
@postgres
async def test_a_judge_not_yet_saved_is_tried_on_the_calls_named_and_saved_nowhere(
    knocking: Knocking,
) -> None:
    await catalog.configure(
        knocking.gateway.connections.pool, judging(broken("not a word of greeting"))
    )
    first = await a_finished_call(knocking, "CA_try_1")
    second = await a_finished_call(knocking, "CA_try_2")
    unsaved: JsonObject = {"name": "greets", "question": "The agent greeted."}
    answer = tried_of(await tried(knocking, {**unsaved, "calls": [first, second]}))
    assert verdicts_of(answer) == [(first, "greets", "broken"), (second, "greets", "broken")]
    assert answer.evals == 2
    assert answer.cost_usd > 0
    async with knocking.http(knocking.app["sandbox"]) as http:
        listed = await http.get(JUDGES)
    assert own_of(listed) == []
    whole = await knocking.gateway.logs.store.whole(first)
    assert whole[-1].type == "call.ended", "a try writes on no log"


@postgres
async def test_one_of_pinecalls_is_tried_by_its_name_on_the_agents_newest_calls(
    knocking: Knocking,
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, judging(a_hold()))
    call = await a_finished_call(knocking, "CA_try_newest")
    answer = tried_of(await tried(knocking, {"name": "grounded", "last": 1}))
    assert verdicts_of(answer) == [(call, "grounded", "held")]


@postgres
async def test_a_judge_of_the_agents_own_is_tried_by_its_name(knocking: Knocking) -> None:
    await written(knocking, "offers-next-slot")
    call = await a_finished_call(knocking, "CA_try_own")
    answer = tried_of(await tried(knocking, {"name": "offers-next-slot", "calls": [call]}))
    [row] = answer.rows
    assert row.judgment is not None
    assert (row.judgment.name, row.judgment.criteria) == ("offers-next-slot", SLOT["question"])


@postgres
async def test_a_box_with_no_judge_model_tries_nothing_and_says_why(knocking: Knocking) -> None:
    call = await a_finished_call(knocking, "CA_try_unjudged")
    answer = tried_of(await tried(knocking, {"name": "grounded", "calls": [call]}))
    assert verdicts_of(answer) == [(call, "grounded", "skipped")]
    assert (answer.rows[0].not_judged or "").startswith(NO_JUDGE)
    assert (answer.evals, answer.cost_usd) == (0, 0.0)


@postgres
async def test_a_call_still_going_is_not_tried(knocking: Knocking) -> None:
    call = await a_finished_call(knocking, "CA_try_going", over=False)
    answer = tried_of(await tried(knocking, {"name": "grounded", "calls": [call]}))
    assert verdicts_of(answer) == [(call, None, None)]
    assert answer.rows[0].not_judged == "the call has not finished"


@postgres
async def test_a_try_names_the_last_calls_or_the_calls_and_a_judge_that_exists(
    knocking: Knocking,
) -> None:
    both = await tried(knocking, {"name": "grounded", "last": 1, "calls": ["CA_x"]})
    neither = await tried(knocking, {"name": "grounded"})
    nobody = await tried(knocking, {"name": "never-written", "last": 1})
    assert [answer.status_code for answer in (both, neither, nobody)] == [400, 400, 404]

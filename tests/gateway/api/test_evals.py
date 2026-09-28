"""Tests for the eval doors: a suite run through the app, a call replayed or judged, the caller."""

from collections.abc import Mapping

import httpx
import pytest
from livekit import rtc
from websockets.asyncio.client import ClientConnection

from pinecall.domain.names import Json, JsonObject
from pinecall.domain.org import Quotas
from pinecall.domain.scope import Scope
from pinecall.evals import runs
from pinecall.log.store import Claim, log_name
from pinecall.providers import catalog
from pinecall.tenancy import admission, personas
from pinecall.tenancy.personas import Persona
from pinecall.wire.rest.evals import EvalRunResponse
from tests.conftest import AGENT, Knocking, configured, issued, postgres, received_until, sent
from tests.fakes.livekit import Server
from tests.gateway.api.conftest import an_app

RUN = "/v1/evals/run"


RUNS = "/v1/evals/runs"


BOOK: JsonObject = {
    "name": "book_appointment",
    "description": "Reserva la cita y se la lee al paciente.",
    "parameters": {"type": "object", "properties": {"slot": {"type": "string"}}},
    "side_effect": "irreversible",
    "confirm": "Le reservo el {slot}. ¿Lo confirmo?",
}


APURADO: JsonObject = {
    "name": "apurado",
    "goal": "cambiar la cita al martes por la tarde",
    "style": "frases cortas",
}


def golden(name: str, *lines: str, **written: Json) -> JsonObject:
    """A golden as `pinecall test` sends it."""
    return {"name": name, "input": list(lines), **written}


async def scripted(knocking: Knocking, *replies: list[str | dict[str, object]]) -> None:
    """Every stage on acme, the model answering each request with the next reply."""
    await catalog.configure(knocking.gateway.connections.pool, configured(list(replies)))


async def declaring(app: ClientConnection, config: JsonObject) -> None:
    """The app declares the agent's tools or events."""
    await sent(app, "agent.configure", {"config": config})
    await received_until(app, "agent.configured")


async def a_suite(knocking: Knocking, body: Mapping[str, object]) -> httpx.Response:
    async with knocking.http(knocking.app["sandbox"]) as http:
        return await http.post(RUN, json=body, timeout=60)


# ── a suite ──


@postgres
async def test_a_run_over_the_goldens_stores_its_scores_and_answers_the_matrix(
    knocking: Knocking,
) -> None:
    await scripted(knocking, ["Buenos días, soy Clara: la consulta cuesta 45 euros."])
    app = await an_app(knocking)
    goldens = [
        golden("greets", "hola", expect={"says": ["Clara"]}),
        golden("prices", "¿cuánto cuesta?", expect={"says": ["45"], "not": ["gratis"]}),
    ]
    answer = await a_suite(knocking, {"agent": AGENT, "goldens": goldens})
    assert answer.status_code == 200, answer.text
    run = answer.json()
    assert run["status"] == "done"
    assert [call["golden"] for call in run["calls"]] == ["greets", "prices"]
    matrix = run["matrix"]
    assert matrix["goldens"] == ["greets", "prices"]
    assert matrix["models"] == ["declared"]
    assert matrix["metrics"] == ["consent", "heard", "says", "silence"]
    assert matrix["failures"] == []
    assert matrix["judge_calls"] == 0
    async with knocking.http(knocking.app["sandbox"]) as http:
        kept = await http.get(f"{RUNS}/{run['id']}")
    assert kept.json() == run
    await app.close()


# `pinecall test` draws its progress from the row, so the order it is written in is the contract.
@postgres
async def test_the_row_names_each_call_before_its_first_turn_and_its_verdict_as_it_ends(
    knocking: Knocking, monkeypatch: pytest.MonkeyPatch
) -> None:
    await scripted(knocking, ["Hola."])
    app = await an_app(knocking)
    rows: list[tuple[str, int, int]] = []
    put = runs.put

    async def kept(pool: object, scope: Scope, run: EvalRunResponse) -> None:
        rows.append((run.status, len(run.calls), 0 if run.matrix is None else len(run.matrix.runs)))
        await put(knocking.gateway.connections.pool, scope, run)
        assert pool is knocking.gateway.connections.pool

    monkeypatch.setattr(runs, "put", kept)
    answer = await a_suite(
        knocking, {"agent": AGENT, "goldens": [golden("a", "hola"), golden("b", "chau")]}
    )
    assert answer.status_code == 200, answer.text
    assert rows == [
        ("running", 0, 0),
        ("running", 1, 0),
        ("running", 1, 1),
        ("running", 2, 1),
        ("running", 2, 2),
        ("done", 2, 2),
    ]
    await app.close()


@postgres
async def test_a_golden_with_an_event_injects_it_and_is_judged_on_its_reply(
    knocking: Knocking,
) -> None:
    await scripted(knocking, ["Buenos días."], ["Se liberó un turno a las 10:15."])
    app = await an_app(knocking)
    await declaring(app, {"events": [{"name": "slot_freed", "from": ["app"]}]})
    freed = golden(
        "a slot frees up mid-call",
        "hola",
        "¿hay algo antes?",
        events=[{"after_turn": 1, "name": "slot_freed", "data": {"at": "10:15"}}],
        expect={"replies": True},
    )
    run = (await a_suite(knocking, {"agent": AGENT, "goldens": [freed]})).json()
    assert run["matrix"]["metrics"] == ["consent", "heard", "replies"]
    assert run["matrix"]["failures"] == []
    await app.close()


@postgres
async def test_a_golden_whose_event_the_agent_never_declared_is_refused_by_name(
    knocking: Knocking,
) -> None:
    await scripted(knocking, ["hola"])
    app = await an_app(knocking)
    meteorite = golden("a meteorite", "hola", events=[{"name": "meteorite"}])
    refused = await a_suite(knocking, {"agent": AGENT, "goldens": [meteorite]})
    assert refused.status_code == 400
    assert "meteorite" in refused.json()["detail"]
    await app.close()


@postgres
async def test_a_run_against_an_agent_nobody_is_holding_is_a_404(knocking: Knocking) -> None:
    refused = await a_suite(knocking, {"agent": "tienda-sur", "goldens": []})
    assert refused.status_code == 404
    assert "tienda-sur" in refused.json()["detail"]


@postgres
async def test_a_second_run_on_one_agent_is_refused_naming_the_first(knocking: Knocking) -> None:
    app = await an_app(knocking)
    knocking.gateway.evals.running[AGENT] = "run_first"
    refused = await a_suite(knocking, {"agent": AGENT, "goldens": []})
    assert refused.status_code == 409
    assert "run_first" in refused.json()["detail"]
    del knocking.gateway.evals.running[AGENT]
    assert (await a_suite(knocking, {"agent": AGENT, "goldens": []})).status_code == 200
    await app.close()


@postgres
async def test_the_runs_are_read_back_newest_first_one_by_its_own_id_and_since_a_time(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    first = (await a_suite(knocking, {"agent": AGENT, "goldens": []})).json()
    second = (await a_suite(knocking, {"agent": AGENT, "goldens": []})).json()
    async with knocking.http(knocking.app["sandbox"]) as http:
        listed = (await http.get(RUNS)).json()
        after = (await http.get(RUNS, params={"since": first["started_at"]})).json()
        nobody = await http.get(f"{RUNS}/run_nobody_ever_ran")
    async with knocking.http(knocking.app["production"]) as http:
        other_world = (await http.get(RUNS)).json()
    assert [run["id"] for run in listed["runs"]] == [second["id"], first["id"]]
    assert [run["id"] for run in after["runs"]] == [second["id"]]
    assert nobody.status_code == 404
    assert other_world == {"runs": []}
    await app.close()


@postgres
async def test_a_run_of_an_org_past_its_quota_fails_with_the_sentence_and_asks_no_model(
    knocking: Knocking,
) -> None:
    await scripted(knocking, ["Buenos días, soy Clara."])
    app = await an_app(knocking)
    pool = knocking.gateway.connections.pool
    await admission.set_quotas(pool, knocking.org.id, "sandbox", Quotas(llm_tokens=0))
    run = (await a_suite(knocking, {"agent": AGENT, "goldens": [golden("greets", "hola")]})).json()
    assert run["status"] == "failed"
    assert "0 of its 0 llm tokens" in run["error"]
    agents_log = await knocking.gateway.logs.store.whole(log_name(None, AGENT))
    assert "credits.exhausted" in [entry.type for entry in agents_log]
    await app.close()


@postgres
async def test_a_run_whose_app_leaves_fails_with_the_partial_matrix_and_opens_no_golden_after(
    knocking: Knocking, monkeypatch: pytest.MonkeyPatch
) -> None:
    await scripted(knocking, ["Buenos días, soy Clara."])
    app = await an_app(knocking)
    put = runs.put
    sockets = knocking.gateway.sockets

    # What the app socket's close does, at the moment the first golden is judged.
    async def leaving(pool: object, scope: Scope, run: EvalRunResponse) -> None:
        await put(knocking.gateway.connections.pool, scope, run)
        if run.matrix is not None and len(run.matrix.runs) == 1:
            for owner in list(sockets.owned):
                await sockets.release(owner)
        assert pool is knocking.gateway.connections.pool

    monkeypatch.setattr(runs, "put", leaving)
    three = [golden("greets", "hola"), golden("prices", "¿cuánto?"), golden("bye", "adiós")]
    run = (await a_suite(knocking, {"agent": AGENT, "goldens": three})).json()
    assert run["status"] == "failed"
    assert "the app detached after 1 of 3 goldens (clinica-norte)" in run["error"]
    assert [opened["golden"] for opened in run["calls"]] == ["greets"]
    assert run["matrix"]["goldens"] == ["greets"]
    await app.close()


@postgres
async def test_a_golden_pins_the_day_the_model_is_told_it_is(knocking: Knocking) -> None:
    await scripted(knocking, ["Buenos días."])
    app = await an_app(knocking)
    pinned = golden("martes", "hola", today="2026-09-08", expect={"says": ["nunca dicho"]})
    run = (await a_suite(knocking, {"agent": AGENT, "goldens": [pinned]})).json()
    [cell] = run["matrix"]["runs"]
    assert "2026-09-08" in str(cell["asked"])
    assert "tuesday" in str(cell["asked"])
    await app.close()


@postgres
async def test_a_spoken_run_takes_the_declared_model_and_the_real_day_or_nothing(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    two_models = {
        "agent": AGENT,
        "goldens": [],
        "voice": True,
        "models": [{"provider": "acme", "model": "acme-2"}],
    }
    pinned = {"agent": AGENT, "goldens": [golden("g", "hola", today="2026-09-08")], "voice": True}
    refused = await a_suite(knocking, two_models)
    assert refused.status_code == 400
    assert "Drop --model, or drop --voice" in refused.json()["detail"]
    refused = await a_suite(knocking, pinned)
    assert refused.status_code == 400
    assert "golden g pins today=2026-09-08" in refused.json()["detail"]
    await app.close()


@postgres
async def test_a_column_per_model_names_each_model_the_run_was_asked_for(
    knocking: Knocking,
) -> None:
    await scripted(knocking, ["hola"])
    app = await an_app(knocking)
    body = {
        "agent": AGENT,
        "goldens": [golden("greets", "hola")],
        "models": [
            {"provider": "acme", "model": "acme-2"},
            {"provider": "acme", "model": "acme-3"},
        ],
    }
    run = (await a_suite(knocking, body)).json()
    assert run["matrix"]["models"] == ["acme/acme-2", "acme/acme-3"]
    assert [opened["model"] for opened in run["calls"]] == ["acme/acme-2", "acme/acme-3"]
    await app.close()


# ── a finished call ──


async def a_finished_call(
    knocking: Knocking, *lines: tuple[str, JsonObject], over: bool = True
) -> str:
    call = f"CA_{len(lines)}_{knocking.org.id[-6:]}"
    scope = Scope(knocking.org.id, "sandbox")
    await knocking.gateway.logs.store.claim(call, AGENT, scope.org, Claim(scope))
    log = knocking.gateway.logs.writing(call, AGENT)
    for kind, data in lines:
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


BOOKED: tuple[tuple[str, JsonObject], ...] = (
    ("turn.user", {"speech_id": "sp_1", "text": "Quiero el jueves", "metrics": {}}),
    (
        "tool.call",
        {"call_id": "c1", "name": "book_appointment", "arguments": {}, "speech_id": "sp_1"},
    ),
    ("tool.result", {"call_id": "c1", "name": "book_appointment", "output": "BK-1"}),
    (
        "turn.agent",
        {
            "speech_id": "sp_1",
            "text": "Reservado, le espero el jueves.",
            "interrupted": False,
            "metrics": {"e2e_latency": 1.0},
        },
    ),
)


@postgres
async def test_a_call_this_runtime_wrote_today_reads_deferred_and_still_passes(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    await declaring(app, {"tools": [BOOK]})
    call = await a_finished_call(knocking, *BOOKED)
    async with knocking.http(knocking.app["sandbox"]) as http:
        answer = await http.post(f"/v1/evals/replay/{call}", json={"banned": ["tienes"]})
    body = answer.json()
    assert answer.status_code == 200, answer.text
    assert (body["call"], body["agent"], body["passed"]) == (call, AGENT, True)
    assert {verdict["check"]: verdict["status"] for verdict in body["verdicts"]} == {
        "consent": "deferred",
        "register": "held",
        "errors": "held",
        "latency": "held",
    }
    await app.close()


@postgres
async def test_a_call_whose_agent_nobody_holds_now_is_replayed_with_consent_skipped(
    knocking: Knocking,
) -> None:
    call = await a_finished_call(knocking, *BOOKED)
    async with knocking.http(knocking.app["sandbox"]) as http:
        body = (await http.post(f"/v1/evals/replay/{call}")).json()
    assert body["verdicts"][0] == {
        "check": "consent",
        "status": "skipped",
        "detail": body["verdicts"][0]["detail"],
    }


@postgres
async def test_an_id_nobody_wrote_under_is_a_404_and_never_a_call_that_passed(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as http:
        refused = await http.post("/v1/evals/replay/CA_nope")
    assert refused.status_code == 404
    assert "CA_nope" in refused.json()["detail"]


@postgres
async def test_another_orgs_call_is_the_same_404_as_nobodys_and_no_key_is_a_401(
    knocking: Knocking,
) -> None:
    call = await a_finished_call(knocking, *BOOKED)
    shop = await issued(
        knocking.gateway.connections.pool, "org_shop", "sandbox", frozenset({"evals"})
    )
    async with knocking.http(shop) as http:
        theirs = await http.post(f"/v1/evals/replay/{call}")
    async with httpx.AsyncClient(base_url=knocking.url) as anonymous:
        nobody = await anonymous.post(f"/v1/evals/replay/{call}")
    assert theirs.status_code == 404
    assert nobody.status_code == 401


# ── a finished call judged again ──


@postgres
async def test_a_call_its_org_did_not_judge_is_judged_and_the_verdict_lands_on_its_log(
    knocking: Knocking,
) -> None:
    call = await a_finished_call(knocking, *BOOKED)
    log = knocking.gateway.logs.writing(call, AGENT)
    await log.append("call.score", {"judges": [], "judge_calls": 0, "not_judged": "off"})
    async with knocking.http(knocking.app["sandbox"]) as http:
        answer = await http.post(f"/v1/evals/judge/{call}")
    assert answer.status_code == 200, answer.text
    assert answer.json()["panel"] == ["consent", "grounded", "promises"]
    scores = [
        entry
        for entry in await knocking.gateway.logs.store.whole(call)
        if entry.type == "call.score"
    ]
    assert len(scores) == 2
    facts = await knocking.gateway.logs.store.whole(call)
    assert facts[-1].data["judges"]


@postgres
async def test_a_call_already_judged_is_judged_again_only_when_asked_to(
    knocking: Knocking,
) -> None:
    call = await a_finished_call(knocking, *BOOKED)
    log = knocking.gateway.logs.writing(call, AGENT)
    await log.append("call.score", {"judges": [], "judge_calls": 0, "passed": True})
    async with knocking.http(knocking.app["sandbox"]) as http:
        refused = await http.post(f"/v1/evals/judge/{call}")
        again = await http.post(f"/v1/evals/judge/{call}", params={"again": "true"})
    assert refused.status_code == 409
    assert "?again=true" in refused.json()["detail"]
    assert again.status_code == 200


@postgres
async def test_a_call_still_going_is_not_judged(knocking: Knocking) -> None:
    call = await a_finished_call(knocking, *BOOKED, over=False)
    async with knocking.http(knocking.app["sandbox"]) as http:
        refused = await http.post(f"/v1/evals/judge/{call}")
    assert refused.status_code == 409
    assert "still going" in refused.json()["detail"]


@postgres
async def test_a_key_without_evals_is_refused_at_the_judge(knocking: Knocking) -> None:
    call = await a_finished_call(knocking, *BOOKED)
    reader = await issued(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", frozenset({"calls"})
    )
    async with knocking.http(reader) as http:
        refused = await http.post(f"/v1/evals/judge/{call}")
    assert refused.status_code == 403


# ── the simulated caller ──


def said_next(line: str, *, hanging_up: bool = False) -> list[str | dict[str, object]]:
    return [{"name": "say_next_line", "arguments": {"line": line, "hanging_up": hanging_up}}]


async def next_line(knocking: Knocking, persona: JsonObject) -> httpx.Response:
    async with knocking.http(knocking.app["sandbox"]) as http:
        return await http.post("/v1/evals/caller", json={"persona": persona, "turns_left": 3})


@postgres
async def test_the_line_the_model_said_comes_back_with_its_hangup(knocking: Knocking) -> None:
    await scripted(knocking, said_next("el martes por la tarde", hanging_up=True))
    answer = await next_line(knocking, APURADO)
    assert (answer.status_code, answer.json()) == (
        200,
        {"say": "el martes por la tarde", "hangup": True},
    )


@postgres
async def test_a_model_this_box_has_no_vendor_for_is_refused_before_anything_is_asked(
    knocking: Knocking,
) -> None:
    refused = await next_line(knocking, {**APURADO, "llm": "openai-but-misspelt/gpt-5"})
    assert refused.status_code == 400
    assert "no vendor named" in refused.json()["detail"]


@postgres
async def test_a_model_that_called_nothing_is_a_502_and_not_a_line_nobody_said(
    knocking: Knocking,
) -> None:
    await scripted(knocking, ["no pienso llamar a la herramienta"])
    refused = await next_line(knocking, APURADO)
    assert refused.status_code == 502
    assert "say_next_line" in refused.json()["detail"]


@postgres
async def test_a_vendor_nobody_keyed_is_the_box_not_being_able_to_play_the_caller(
    knocking: Knocking,
) -> None:
    box = configured().model_dump(mode="json")
    box["defaults"]["llm"] = {"vendor": "openai", "model": "gpt-5"}
    await catalog.configure(
        knocking.gateway.connections.pool, catalog.Providers.model_validate(box)
    )
    refused = await next_line(knocking, APURADO)
    assert refused.status_code == 503


# ── a spoken call ──


@postgres
async def test_a_persona_written_for_other_agents_is_not_put_on_this_ones_line(
    knocking: Knocking,
) -> None:
    written = Persona(name="apurado", goal="g", style="s", agents=frozenset({"tienda-sur"}))
    await personas.put_persona(
        knocking.gateway.connections.pool, knocking.org.id, written, author="a"
    )
    async with knocking.http(knocking.app["sandbox"]) as http:
        refused = await http.post(
            "/v1/evals/voice", json={"call": "call_1", "agent": AGENT, "persona": APURADO}
        )
    assert refused.status_code == 400
    assert "written for tienda-sur, not for clinica-norte" in refused.json()["detail"]


# The media plane is the box's: here the room refuses the caller, and the room still goes.
@postgres
async def test_the_agent_is_dispatched_with_the_persona_and_the_room_is_deleted_whatever_happens(
    knocking: Knocking, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refused(_room: rtc.Room, _url: str, _token: str) -> None:
        raise rtc.ConnectError("the room refused the caller")

    monkeypatch.setattr(rtc.Room, "connect", refused)
    ruled = {**APURADO, "accepts_when": "a Tuesday slot"}
    async with knocking.http(knocking.app["sandbox"]) as http:
        answer = await http.post(
            "/v1/evals/voice", json={"call": "call_1", "agent": AGENT, "persona": ruled}
        )
    assert answer.status_code == 503
    assert "could not be held" in answer.json()["detail"]
    server = knocking.gateway.connections.server
    assert isinstance(server, Server)
    [dispatch] = server.dispatcher.made
    assert dispatch.room == "call_1"
    assert dispatch.agent_name == "pinecall-sandbox"
    assert '"persona":"apurado"' in dispatch.metadata
    assert '"accepts_when":"a Tuesday slot"' in dispatch.metadata
    assert [str(getattr(request, "room", "")) for request in server.rooms.requests] == ["call_1"]

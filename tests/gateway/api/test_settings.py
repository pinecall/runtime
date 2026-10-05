"""Tests for the settings doors: an agent's tuning and lexicon per scope, and a call's own."""

from pinecall.domain.agent import Tuning, Turn
from pinecall.domain.scope import Scope
from pinecall.tenancy import scopes
from pinecall.tenancy.scopes import Written
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import AGENT, Knocking, a_developer, issued, postgres
from tests.gateway.api.conftest import a_call, an_app

SETTINGS = f"/v1/agents/{AGENT}/settings"
LEXICON = f"/v1/agents/{AGENT}/lexicon"
A_SET = {"config": {"llm": "acme/acme-1", "greeting": {"say": "Hola"}, "record": True}}


@postgres
async def test_a_set_is_the_teams_newest_and_the_next_one_needs_the_version_read(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        first = await org.put(SETTINGS, json=A_SET)
        stale = await org.put(SETTINGS, json={**A_SET, "if_version": 0})
        second = await org.put(SETTINGS, json={**A_SET, "if_version": 1, "note": "louder"})
        read = await org.get(SETTINGS)
    assert first.status_code == 200
    assert first.json()["world"] == "sandbox"
    assert first.json()["yours"] is None
    assert first.json()["team"]["version"] == 1
    assert first.json()["team"]["config"] == A_SET["config"]
    assert first.json()["production"] is None
    assert stale.status_code == 409
    assert "v1 now" in stale.json()["detail"]
    assert (second.json()["team"]["version"], second.json()["team"]["note"]) == (2, "louder")
    assert read.json()["team"]["version"] == 2


@postgres
async def test_a_developer_writes_their_own_scope_unless_they_say_team(
    knocking: Knocking,
) -> None:
    member, key = await a_developer(knocking, "dev@clinica.test")
    async with knocking.http(key) as developer:
        mine = await developer.put(SETTINGS, json=A_SET, headers={"pinecall-env": "sandbox"})
        teams = await developer.put(
            SETTINGS, json={**A_SET, "team": True}, headers={"pinecall-env": "sandbox"}
        )
        history = await developer.get(f"{SETTINGS}/history", headers={"pinecall-env": "sandbox"})
    assert mine.json()["yours"]["holder"] == member
    assert mine.json()["team"] is None
    assert teams.json()["team"]["holder"] == ""
    assert teams.json()["yours"]["holder"] == member
    assert (history.json()["holder"], len(history.json()["rows"])) == (member, 1)


@postgres
async def test_a_words_key_sets_the_opening_words_and_is_refused_the_pipeline_by_name(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    words = await issued(pool, knocking.org.id, "sandbox", frozenset({"words"}))
    async with knocking.http(knocking.app["sandbox"]) as org:
        await org.put(SETTINGS, json=A_SET)
    async with knocking.http(words) as supervisor:
        spoken = await supervisor.put(SETTINGS, json={"config": {"greeting": {"say": "Buenas"}}})
        refused = await supervisor.put(
            SETTINGS, json={"config": {"llm": "acme/acme-2", "turn": {"endpointing_ms": 900}}}
        )
        history = await supervisor.get(f"{SETTINGS}/history")
    assert spoken.status_code == 200
    kept = spoken.json()["team"]["config"]
    assert kept["greeting"] == {"say": "Buenas"}
    assert (kept["llm"], kept["record"]) == ("acme/acme-1", True)
    assert refused.status_code == 403
    assert "llm, turn: the pipeline's" in refused.json()["detail"]
    assert [row["version"] for row in history.json()["rows"]] == [2, 1]


@postgres
async def test_the_models_deadline_is_kept_as_set_and_is_the_pipelines_to_set(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    words = await issued(pool, knocking.org.id, "sandbox", frozenset({"words"}))
    async with knocking.http(knocking.app["sandbox"]) as org:
        kept = await org.put(SETTINGS, json={"config": {"llm_timeout_s": 6.5}})
        zero = await org.put(SETTINGS, json={"config": {"llm_timeout_s": 0}})
    async with knocking.http(words) as supervisor:
        refused = await supervisor.put(SETTINGS, json={"config": {"llm_timeout_s": 3}})
    assert kept.json()["team"]["config"] == {"llm_timeout_s": 6.5}
    assert zero.status_code == 400
    assert "positive number of seconds" in zero.json()["detail"]
    assert refused.status_code == 403
    assert "llm_timeout_s: the pipeline's" in refused.json()["detail"]


@postgres
async def test_a_knob_the_vendor_takes_under_no_name_is_refused_where_it_is_set(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        eager = await org.put(SETTINGS, json={"config": {"turn": {"eager_eot_threshold": 0.4}}})
        taken = await org.put(SETTINGS, json={"config": {"turn": {"eot_threshold": 0.8}}})
    assert eager.status_code == 400
    assert "acme's stt takes no eager_eot_threshold" in eager.json()["detail"]
    assert taken.status_code == 200


@postgres
async def test_a_threshold_outside_the_band_the_ears_take_is_refused_where_it_is_set(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        low = await org.put(SETTINGS, json={"config": {"turn": {"eot_threshold": 0.1}}})
        high = await org.put(SETTINGS, json={"config": {"turn": {"eot_threshold": 0.95}}})
        edge = await org.put(SETTINGS, json={"config": {"turn": {"eot_threshold": 0.5}}})
    assert low.status_code == 400
    assert "eot_threshold 0.1 is outside 0.5 to 0.9" in low.json()["detail"]
    assert high.status_code == 400
    assert edge.status_code == 200, edge.text


@postgres
async def test_a_words_key_is_never_refused_a_knob_it_carried_over_untouched(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    stored = Tuning(turn=Turn(eager_eot_threshold=0.4))
    await scopes.put_tuning(pool, Scope(knocking.org.id, "sandbox"), AGENT, stored, Written("m"))
    words = await issued(pool, knocking.org.id, "sandbox", frozenset({"words"}))
    async with knocking.http(words) as supervisor:
        spoken = await supervisor.put(SETTINGS, json={"config": {"greeting": {"say": "Buenas"}}})
    assert spoken.status_code == 200, spoken.text
    assert spoken.json()["team"]["config"]["turn"] == {"eager_eot_threshold": 0.4}


@postgres
async def test_the_diff_names_the_fields_this_scope_differs_in_from_production(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as production:
        await production.put(SETTINGS, json={"config": {"llm": "acme/acme-1", "record": False}})
    async with knocking.http(knocking.app["sandbox"]) as sandbox:
        await sandbox.put(SETTINGS, json=A_SET)
        diff = await sandbox.get(f"{SETTINGS}/diff")
        same = await sandbox.get(f"{SETTINGS}/diff", params={"against": "team"})
    assert diff.json()["changed"] == ["greeting", "record"]
    assert diff.json()["theirs"]["config"] == {"llm": "acme/acme-1", "record": False}
    assert same.json()["changed"] == []


@postgres
async def test_a_rollback_is_a_new_version_copying_the_old_one(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        await org.put(SETTINGS, json=A_SET)
        await org.put(SETTINGS, json={"config": {"llm": "acme/acme-2"}})
        back = await org.post(f"{SETTINGS}/rollback", json={"version": 1})
        gone = await org.post(f"{SETTINGS}/rollback", json={"version": 9})
    assert back.json()["team"]["version"] == 3
    assert back.json()["team"]["config"] == A_SET["config"]
    assert back.json()["team"]["note"] == "rollback to v1"
    assert gone.status_code == 404


@postgres
async def test_a_set_naming_a_vendor_nobody_installed_is_refused_where_it_is_written(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["sandbox"]) as org:
        refused = await org.put(SETTINGS, json={"config": {"llm": "nobody/model-1"}})
        blank = await org.put(SETTINGS, json={"config": {"voice": "  "}})
    assert refused.status_code >= 400
    assert "nobody" in refused.json()["detail"]
    assert blank.status_code == 400
    assert "voice" in blank.json()["detail"]


@postgres
async def test_a_call_answers_the_versions_it_was_built_on(knocking: Knocking) -> None:
    app = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as org:
        await org.put(SETTINGS, json=A_SET)
        await org.put(LEXICON, json={"lexicon": {"said": [], "heard": ["Vidal"]}})
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
    async with knocking.http(knocking.app["sandbox"]) as org:
        built = await org.get(f"/v1/calls/{context.call}/settings")
        nobody = await org.get("/v1/calls/CA_nobody/settings")
    assert built.status_code == 200
    body = built.json()
    assert (body["config_version"], body["lexicon_version"]) == (1, 1)
    assert body["config"]["config"] == A_SET["config"]
    assert body["lexicon"]["lexicon"] == {"said": [], "heard": ["Vidal"]}
    assert nobody.status_code == 404
    await app.close()


CANARY = f"{SETTINGS}/canary"


@postgres
async def test_a_canary_is_the_pipelines_to_set_and_a_version_the_scope_never_had_is_404(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    words = await issued(pool, knocking.org.id, "sandbox", frozenset({"words"}))
    async with knocking.http(knocking.app["sandbox"]) as org:
        await org.put(SETTINGS, json=A_SET)
        await org.put(SETTINGS, json={**A_SET, "if_version": 1})
        none = await org.get(CANARY)
        set_ = await org.put(CANARY, json={"version": 2, "share": 10, "note": "try v2"})
        missing = await org.put(CANARY, json={"version": 9, "share": 10})
        over = await org.put(CANARY, json={"version": 2, "share": 101})
    async with knocking.http(words) as supervisor:
        read = await supervisor.get(CANARY)
        refused = await supervisor.put(CANARY, json={"version": 2, "share": 50})
        not_cleared = await supervisor.delete(CANARY)
    async with knocking.http(knocking.app["sandbox"]) as org:
        cleared = await org.delete(CANARY)
    assert none.json() == {"world": "sandbox", "holder": "", "canary": None}
    kept = set_.json()["canary"]
    assert (kept["version"], kept["share"], kept["note"], kept["holder"]) == (2, 10, "try v2", "")
    assert read.json()["canary"] == kept
    assert missing.status_code == 404
    assert over.status_code in {400, 422}
    assert (refused.status_code, not_cleared.status_code) == (403, 403)
    assert cleared.json()["canary"] is None


@postgres
async def test_a_call_on_the_canarys_share_runs_its_version_and_says_so(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as org:
        await org.put(SETTINGS, json=A_SET)
        await org.put(SETTINGS, json={**A_SET, "if_version": 1, "note": "the candidate"})
        await org.put(CANARY, json={"version": 2, "share": 100})
    on_canary = await _opened(knocking)
    async with knocking.http(knocking.app["sandbox"]) as org:
        await org.put(CANARY, json={"version": 2, "share": 0})
    on_rest = await _opened(knocking)
    async with knocking.http(knocking.app["sandbox"]) as org:
        first = (await org.get(f"/v1/calls/{on_canary}/settings")).json()
        second = (await org.get(f"/v1/calls/{on_rest}/settings")).json()
    assert (first["config_version"], first["canary"]) == (2, True)
    assert (second["config_version"], second["canary"]) == (1, False)
    await app.close()


async def _opened(knocking: Knocking) -> str:
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        opened = await worker.post(
            "/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written()
        )
    assert opened.status_code == 200, opened.text
    return context.call


@postgres
async def test_the_lexicon_is_set_read_and_kept_as_versions(knocking: Knocking) -> None:
    words = {"said": [{"word": "Vidal", "spoken": "Bidál"}], "heard": ["Vidal", "ortodoncia"]}
    async with knocking.http(knocking.app["sandbox"]) as org:
        first = await org.put(LEXICON, json={"lexicon": words})
        stale = await org.put(LEXICON, json={"lexicon": words, "if_version": 0})
        read = await org.get(LEXICON)
        history = await org.get(f"{LEXICON}/history", params={"limit": 5})
    assert first.status_code == 200
    assert first.json()["team"]["lexicon"] == words
    assert stale.status_code == 409
    assert read.json()["team"]["version"] == 1
    assert read.json()["yours"] is None
    assert (history.json()["holder"], [row["version"] for row in history.json()["rows"]]) == (
        "",
        [1],
    )


@postgres
async def test_a_lexicon_is_one_agents_and_another_agent_reads_none(knocking: Knocking) -> None:
    words = {"said": [], "heard": ["Vidal"]}
    async with knocking.http(knocking.app["sandbox"]) as org:
        await org.put(LEXICON, json={"lexicon": words})
        other = await org.get("/v1/agents/turnos/lexicon")
        history = await org.get("/v1/agents/turnos/lexicon/history")
        written = await org.put("/v1/agents/turnos/lexicon", json={"lexicon": words})
    assert (other.json()["team"], other.json()["yours"]) == (None, None)
    assert history.json()["rows"] == []
    assert written.json()["team"]["version"] == 1


@postgres
async def test_a_words_key_writes_the_agents_lexicon(knocking: Knocking) -> None:
    pool = knocking.gateway.connections.pool
    words = await issued(pool, knocking.org.id, "sandbox", frozenset({"words"}))
    async with knocking.http(words) as supervisor:
        written = await supervisor.put(LEXICON, json={"lexicon": {"said": [], "heard": ["GSA"]}})
    assert written.status_code == 200
    assert written.json()["team"]["lexicon"]["heard"] == ["GSA"]

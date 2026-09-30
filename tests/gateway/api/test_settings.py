"""Tests for the settings doors: an agent's tuning and lexicon per scope, and a call's own."""

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

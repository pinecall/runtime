"""POST /v1/evals/run: goldens driven through the connected app, scored, stored and read back."""

import asyncio
from typing import Any, override

import httpx
import pytest

from pinecall.api._deps import the_runs
from pinecall.api.agents.registry import Registry
from pinecall.api.app import app
from pinecall.api.evals.runner import AlreadyRunning, Runner

# The judges are the `evals` group, not a dependency of the gateway: on a box without it the door
# answers 503 and this file has nothing to assert. The whole module skips, naming the command.
from pinecall.evals import a_case
from pinecall.evals.runs import EvalRun, MemoryRuns
from pinecall.log.replay import whole
from pinecall.log.store import MemoryStore
from pinecall.orgs.table import MemoryOrgs
from pinecall.orgs.vault import Vault
from pinecall.types import ProviderKeys, Quotas
from pinecall_protocol import defs
from tests.api.conftest import AN_ORG
from tests.api.evals.conftest import (
    AGENT,
    ANOTHER_AGENT,
    ANOTHER_OWNER,
    RUN,
    a_golden,
    serving,
)
from tests.session.fake_llm import FakeLLM, Scripted

pytestmark = pytest.mark.unit

RUNS = "/v1/evals/runs"

# How long a test waits for the other agent's run to reach the same point before it gives up. A
# gateway that admits one run at a time never gets there, and the wait must fail rather than hang.
A_MOMENT_S = 5.0

# `from` is a keyword, so this one declaration is built from the wire's own key names.
A_FREED_SLOT = defs.EventSpec.model_validate({"name": "slot_freed", "from": ["app"]})

# The clinic's own account with the vendor, as one row of the vault holds it.
THE_CLINICS_OWN = "sk-the-clinics-own-anthropic-account"


async def test_a_run_over_the_goldens_stores_its_scores_and_answers_the_matrix(
    suite_http: httpx.AsyncClient, registry: Registry, llm: FakeLLM, eval_runs: MemoryRuns
) -> None:
    """Criterion 1: two goldens in, one matrix out, and the same run readable from the store."""
    await serving(registry)
    llm.script.extend(
        (
            Scripted(chunks=("Buenos días, soy Clara.",)),
            Scripted(chunks=("La consulta cuesta 45 euros.",)),
        )
    )
    goldens = [
        a_golden("greets", ["hola"], expect={"says": ["Clara"]}),
        a_golden("prices", ["¿cuánto cuesta?"], expect={"says": ["45"], "not": ["gratis"]}),
    ]

    answered = await suite_http.post(RUN, json={"agent": AGENT, "goldens": goldens})

    assert answered.status_code == 200, answered.text
    run: dict[str, Any] = answered.json()
    assert run["status"] == "done"
    assert [call["golden"] for call in run["calls"]] == ["greets", "prices"]
    matrix = run["matrix"]
    assert matrix["goldens"] == ["greets", "prices"]
    # `consent` leads every row whatever the golden asked for: api/evals/scoring.py.
    assert matrix["metrics"] == ["consent", "heard", "says", "silence"]
    assert not matrix["failures"]
    assert [score["score"] for row in matrix["runs"] for score in row["scores"]] == [1.0] * 7
    # Every one of those judges answers by code, so nothing was ever asked of a model.
    assert matrix["judge_calls"] == 0
    kept = await eval_runs.of(run["id"])
    assert kept is not None and kept.matrix == matrix


# What `pinecall test` draws while the run happens is read off this row, so the order it is written
# in is the contract: the call is on it before the first turn, its verdict the moment it ends.
class Rewritten(MemoryRuns):
    """The runs store, keeping every row as it was written and not only the last."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[tuple[str, int, int]] = []

    @override
    async def put(self, run: EvalRun) -> None:
        await super().put(run)
        judged = 0 if run.matrix is None else len(run.matrix["runs"])
        self.rows.append((run.status, len(run.calls), judged))


async def test_the_row_names_each_call_before_its_first_turn_and_its_verdict_as_it_ends(
    suite_http: httpx.AsyncClient, registry: Registry, llm: FakeLLM, eval_runs: MemoryRuns
) -> None:
    """A watcher polling the row sees the golden being driven, then its cell, not all at the end."""
    await serving(registry)
    rewritten = Rewritten()
    app.dependency_overrides[the_runs] = lambda: rewritten
    llm.script.extend((Scripted(chunks=("Hola.",)), Scripted(chunks=("Adiós.",))))
    goldens = [a_golden("greets", ["hola"]), a_golden("parts", ["chau"])]

    answered = await suite_http.post(RUN, json={"agent": AGENT, "goldens": goldens})

    assert answered.status_code == 200, answered.text
    assert rewritten.rows == [
        ("running", 0, 0),
        ("running", 1, 0),
        ("running", 1, 1),
        ("running", 2, 1),
        ("running", 2, 2),
        ("done", 2, 2),
    ]
    assert await eval_runs.of(answered.json()["id"]) is None


async def test_a_golden_with_an_event_injects_it_at_the_declared_turn(
    suite_http: httpx.AsyncClient, registry: Registry, llm: FakeLLM, store: MemoryStore
) -> None:
    """Criterion 2: call.event lands between the turns, and the case carries it and the reply."""
    await serving(registry, events=[A_FREED_SLOT])
    llm.script.extend(
        (
            Scripted(chunks=("Buenos días.",)),
            Scripted(chunks=("Se liberó un turno a las 10:15.",)),
        )
    )
    golden = a_golden(
        "a slot frees up mid-call",
        ["hola", "¿hay algo antes?"],
        events=[{"after_turn": 1, "name": "slot_freed", "data": {"at": "10:15"}}],
        expect={"replies": True},
    )

    answered = await suite_http.post(RUN, json={"agent": AGENT, "goldens": [golden]})

    assert answered.status_code == 200, answered.text
    run: dict[str, Any] = answered.json()
    assert run["matrix"]["metrics"] == ["consent", "heard", "replies"]
    assert not run["matrix"]["failures"]

    entries = await whole(store, run["calls"][0]["call"])
    types = [entry.type for entry in entries]
    said_by_the_caller = [at for at, type in enumerate(types) if type == "turn.user"]
    answered = [at for at, type in enumerate(types) if type == "turn.agent"]
    # After the first exchange and before the caller's second turn: that is what `after_turn` says.
    assert answered[0] < types.index("event.received") < said_by_the_caller[1]

    case = a_case(entries)
    arrived = case.events
    assert [(fact.name, fact.data, fact.source) for fact in arrived] == [
        ("slot_freed", {"at": "10:15"}, "app")
    ]
    said_after = [
        turn.text for turn in case.turns if turn.role == "assistant" and turn.seq > arrived[0].seq
    ]
    assert said_after == ["Se liberó un turno a las 10:15."]


async def test_a_golden_whose_event_the_agent_never_declared_is_refused_by_name(
    suite_http: httpx.AsyncClient, registry: Registry
) -> None:
    """The runner injects a fact through the app socket's own rule, so the same name is refused."""
    await serving(registry, events=[A_FREED_SLOT])
    golden = a_golden(
        "a meteorite", ["hola"], events=[{"after_turn": 0, "name": "meteorite", "data": {}}]
    )

    answered = await suite_http.post(RUN, json={"agent": AGENT, "goldens": [golden]})

    assert answered.status_code == 400
    assert "meteorite" in answered.json()["detail"]


async def test_a_run_against_an_agent_nobody_is_holding_is_a_404(
    suite_http: httpx.AsyncClient,
) -> None:
    """Nothing to evaluate: no app is serving that slug, and the answer says which slug."""
    answered = await suite_http.post(RUN, json={"agent": ANOTHER_AGENT, "goldens": []})

    assert answered.status_code == 404
    assert ANOTHER_AGENT in answered.json()["detail"]


async def test_a_second_run_on_one_agent_is_refused_and_that_agent_is_freed_after() -> None:
    """One at a time per agent: the refusal names the run and the agent, and the hold is let go."""
    runner = Runner()

    async with runner.alone("run_first", AGENT):
        assert runner.in_flight(AGENT) == "run_first"
        with pytest.raises(AlreadyRunning) as refused:
            async with runner.alone("run_second", AGENT):
                pass

    assert "run_first" in str(refused.value)
    assert AGENT in str(refused.value)
    assert runner.in_flight(AGENT) is None


async def test_a_run_on_one_agent_leaves_every_other_agent_free_to_be_run() -> None:
    """Criterion 1, the lock itself: a hold is one agent's, taken and freed one agent at a time."""
    runner = Runner()

    async with runner.alone("run_the_clinics", AGENT):
        async with runner.alone("run_the_shops", ANOTHER_AGENT):
            assert runner.in_flight(AGENT) == "run_the_clinics"
            assert runner.in_flight(ANOTHER_AGENT) == "run_the_shops"
        # The shop's run ended; the clinic's is still going, and its hold did not end with it.
        assert runner.in_flight(ANOTHER_AGENT) is None
        assert runner.in_flight(AGENT) == "run_the_clinics"

    assert runner.in_flight(AGENT) is None


# Two runs held at their first written row until BOTH are there: under a gateway-wide lock the
# second is refused before it writes one, so the first waits out the timeout and the test says so
# rather than passing on two runs that never overlapped.
class Rendezvous(MemoryRuns):
    """The runs store, holding each run at its opening row until the other agent's run is there."""

    def __init__(self) -> None:
        super().__init__()
        self.both_running = asyncio.Barrier(2)

    @override
    async def put(self, run: EvalRun) -> None:
        await super().put(run)
        if run.status == "running" and not run.calls:
            async with asyncio.timeout(A_MOMENT_S):
                await self.both_running.wait()


async def test_two_agents_held_by_two_apps_are_evaluated_at_the_same_time(
    suite_http: httpx.AsyncClient, registry: Registry
) -> None:
    """Criterion 1: two devs, two agents, one gateway — neither run waits for the other's."""
    await serving(registry)
    await serving(registry, slug=ANOTHER_AGENT, owner=ANOTHER_OWNER)
    rendezvous = Rendezvous()
    app.dependency_overrides[the_runs] = lambda: rendezvous

    both = await asyncio.gather(
        suite_http.post(RUN, json={"agent": AGENT, "goldens": []}),
        suite_http.post(RUN, json={"agent": ANOTHER_AGENT, "goldens": []}),
    )

    assert [answered.status_code for answered in both] == [200, 200], [one.text for one in both]
    runs: list[dict[str, Any]] = [answered.json() for answered in both]
    assert [run["agent"] for run in runs] == [AGENT, ANOTHER_AGENT]
    assert [run["status"] for run in runs] == ["done", "done"]


async def test_the_runs_are_read_back_newest_first_and_one_by_its_own_id(
    suite_http: httpx.AsyncClient, registry: Registry
) -> None:
    """The two GET doors: the list a drift check diffs across, and one whole run by id."""
    await serving(registry)
    first = (await suite_http.post(RUN, json={"agent": AGENT, "goldens": []})).json()
    second = (await suite_http.post(RUN, json={"agent": AGENT, "goldens": []})).json()

    listed = await suite_http.get(RUNS)
    assert [run["id"] for run in listed.json()["runs"]] == [second["id"], first["id"]]

    after_the_first = await suite_http.get(RUNS, params={"since": first["started_at"]})
    assert [run["id"] for run in after_the_first.json()["runs"]] == [second["id"]]

    one = await suite_http.get(f"{RUNS}/{first['id']}")
    assert one.json() == first
    assert (await suite_http.get(f"{RUNS}/run_nobody_ever_ran")).status_code == 404


# A run is one org's — the org holding the agent, not whoever's key opened the door — and it drives
# the very same text session a caller does. So it asks the vault the same question, once, as the
# run opens: docs/decisions/provider-keys.md.
async def test_a_run_of_an_org_that_brought_a_key_answers_every_golden_on_it(
    suite_http: httpx.AsyncClient,
    registry: Registry,
    llm: FakeLLM,
    vault: Vault | None,
    keys_asked: list[ProviderKeys],
) -> None:
    """Criterion 1, the run half: the conversations run on the tenant's own account."""
    assert vault is not None
    await vault.put(AN_ORG.id, "anthropic", THE_CLINICS_OWN)
    await serving(registry)
    llm.script.append(Scripted(chunks=("Buenos días, soy Clara.",)))
    goldens = [a_golden("greets", ["hola"], expect={"says": ["Clara"]})]

    answered = await suite_http.post(RUN, json={"agent": AGENT, "goldens": goldens})

    assert answered.status_code == 200, answered.text
    assert keys_asked == [{"anthropic": THE_CLINICS_OWN}]


async def test_a_run_of_an_org_that_brought_none_runs_on_the_boxs_own_keys(
    suite_http: httpx.AsyncClient, registry: Registry, keys_asked: list[ProviderKeys]
) -> None:
    """Managed is the absence of a row here too, and a run with no golden asks for no model."""
    await serving(registry)

    answered = await suite_http.post(RUN, json={"agent": AGENT, "goldens": []})

    assert answered.status_code == 200, answered.text
    assert keys_asked == [{}]


async def test_a_run_of_an_org_past_its_token_quota_fails_with_the_sentence_and_asks_no_model(
    suite_http: httpx.AsyncClient,
    registry: Registry,
    llm: FakeLLM,
    orgs: MemoryOrgs,
    store: MemoryStore,
) -> None:
    """A golden run is a written call of the org's: past `llm_tokens` it is refused like a chat."""
    await serving(registry)
    await orgs.set_quotas(AN_ORG.id, Quotas(llm_tokens=0))
    llm.script.append(Scripted(chunks=("Buenos días, soy Clara.",)))

    answered = await suite_http.post(
        RUN, json={"agent": AGENT, "goldens": [a_golden("greets", ["hola"])]}
    )

    assert answered.status_code == 200, answered.text
    run: dict[str, Any] = answered.json()
    assert run["status"] == "failed"
    assert "0 of its 0 llm_tokens: credits.exhausted" in run["error"]
    assert llm.requests == 0
    refusals = [one for one in await store.agent_since(AGENT) if one.type == "credits.exhausted"]
    assert refusals and refusals[-1].data["quota"] == "llm_tokens"

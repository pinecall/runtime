"""Tests for the seal: the summary and its cost, the judges at hang-up, and what memory kept."""

import asyncio
import dataclasses
import json
import logging
from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.domain.agent import AgentConfig, MemoryPolicy, Model, Versions, block_hash
from pinecall.domain.call import CallContext
from pinecall.domain.judging import JudgeSpec
from pinecall.domain.monitor import Monitor
from pinecall.domain.names import JsonObject
from pinecall.domain.org import Quotas
from pinecall.domain.scope import THE_ORGS_OWN, Scope
from pinecall.domain.webhook import Webhook
from pinecall.evals import dataset
from pinecall.evals.catalog import library
from pinecall.gateway._gateway import Gateway
from pinecall.gateway.calls.serving import opened, served_call
from pinecall.gateway.ending.seal import (
    A_RUN_JUDGES_IT,
    NO_JUDGE,
    drifted,
    prompt_of,
    sealed,
    summed_up,
    surroundings_of,
)
from pinecall.log.logs import log_name
from pinecall.log.reduce import Metered, usage_row
from pinecall.log.store import Claim
from pinecall.providers import catalog
from pinecall.providers.catalog import Embedding, Rate
from pinecall.retrieval import memory
from pinecall.retrieval.embed import Embedder
from pinecall.tenancy import admission, consents, judges, monitors, orgs, vault, webhooks
from pinecall.tenancy.consents import Given
from pinecall.wire.frames import Entry
from pinecall.wire.metrics import LLMModelUsage
from pinecall.wire.parts import ModelConfig
from pinecall.wire.rest.calls import SealCallRequest
from pinecall.wire.scores import CallScore
from tests.conftest import configured, postgres
from tests.fakes.embeddings import Embeddings
from tests.fakes.webhooks import Receiver
from tests.gateway.conftest import (
    AGENT,
    EVERY_HELD,
    NOT_APPLIES,
    OURS,
    a_call,
    a_hold,
    a_start,
    broken,
    judging,
)

COUNTED = "select held, broken, config_version from judge_days where org = %(org)s"

# ── the summary and its cost ──


@postgres
async def test_the_seal_writes_the_summary_the_score_and_lets_the_call_go(wired: Gateway) -> None:
    context = a_call()
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    await served.log.append(
        "call.ended",
        {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 5.0, "duration_s": 30.0},
    )
    used = LLMModelUsage(provider="acme", model="acme-1", input_tokens=1000, output_tokens=500)
    await sealed(
        wired.serving, served, SealCallRequest(usage=[used], outcome="booked"), lent=["acme"]
    )
    whole = await wired.logs.store.whole(context.call)
    summary = next(item for item in whole if item.type == "call.summary")
    assert (summary.data["reason"], summary.data["duration_s"], summary.data["outcome"]) == (
        "caller_hung_up",
        30.0,
        "booked",
    )
    cost = summary.data["cost"]
    assert isinstance(cost, dict)
    assert isinstance(cost["usd"], float)
    assert cost["usd"] > 0
    assert whole[-1].type == "call.score"
    assert await wired.logs.store.sealed(context.call)
    assert context.call not in wired.live.calls


@postgres
async def test_a_call_sealed_twice_at_once_is_summed_up_and_scored_once(wired: Gateway) -> None:
    context = a_call()
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    await served.log.append(
        "call.ended",
        {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 5.0, "duration_s": 30.0},
    )
    sealing = SealCallRequest(usage=[], outcome="booked")
    await asyncio.gather(
        sealed(wired.serving, served, sealing), sealed(wired.serving, served, sealing)
    )
    kinds = [item.type for item in await wired.logs.store.whole(context.call)]
    assert (kinds.count("call.summary"), kinds.count("call.score")) == (1, 1)
    assert await wired.logs.store.sealed(context.call)


# A gateway that took the lease and died: its lease runs out, and a knock waiting takes the seal.
@postgres
async def test_a_seal_whose_gateway_died_holding_the_lease_is_taken_over_once_it_lapses(
    wired: Gateway,
) -> None:
    context = a_call()
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    assert await wired.logs.store.lease_seal(context.call, 1.0)
    await sealed(wired.serving, served, SealCallRequest(usage=[], outcome="booked"))
    kinds = [item.type for item in await wired.logs.store.whole(context.call)]
    assert (kinds.count("call.summary"), kinds.count("call.score")) == (1, 1)
    assert await wired.logs.store.sealed(context.call)


@postgres
async def test_the_seal_counts_the_calls_verdicts_into_its_days_drift_once(wired: Gateway) -> None:
    pool = wired.connections.pool
    org = await orgs.create(pool, "clinica-norte", "Clinica Norte")
    scope = Scope(org.id, "sandbox")
    await catalog.configure(pool, judging(*EVERY_HELD))
    context = a_call(scope)
    await wired.logs.store.claim(context.call, AGENT, org.id, Claim(scope, Versions(config=2)))
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), scope)
    await opened(served.log, context, AGENT)
    await sealed(wired.serving, served, SealCallRequest(usage=[], outcome="booked"))
    whole = await wired.logs.store.whole(context.call)
    score = CallScore.model_validate(whole[-1].data)
    await drifted(pool, context.call, whole, score)
    async with pool.connection() as connection:
        rows = await (await connection.execute(COUNTED, {"org": org.id})).fetchall()
    settled = [judge for judge in score.judges if judge.verdict in {"held", "broken"}]
    assert len(settled) == len([answer for answer in EVERY_HELD if answer != NOT_APPLIES])
    assert sum(row["held"] + row["broken"] for row in rows) == len(settled)
    assert {row["config_version"] for row in rows} == {2}


@postgres
async def test_the_seal_fires_a_monitor_that_crossed_its_line_once_a_day(
    wired: Gateway, receiver: Receiver
) -> None:
    pool = wired.connections.pool
    org = await orgs.create(pool, "clinica-norte", "Clinica Norte")
    scope = Scope(org.id, "sandbox")
    await webhooks.put_webhook(pool, wired.connections.vault, org.id, Webhook(receiver.url))
    busy = Monitor("", "any call at all", "calls", above=True, threshold=0, window_days=1)
    kept = await monitors.put_monitor(pool, scope, busy, "m_ana")
    for _ in range(2):
        context = a_call(scope)
        await wired.logs.store.claim(context.call, AGENT, org.id, Claim(scope, Versions()))
        served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), scope)
        await opened(served.log, context, AGENT)
        await sealed(wired.serving, served, SealCallRequest(usage=[], outcome="booked"))
    fired = [
        entry
        for entry in await wired.logs.store.whole(log_name(None, AGENT))
        if entry.type == "monitor.fired"
    ]
    assert len(fired) == 1, "the second seal finds it fired today"
    assert (fired[0].data["monitor"], fired[0].data["value"]) == (kept.id, 1.0)
    [read] = await monitors.monitors_of(pool, scope)
    assert read.fired_value == 1.0
    [post] = receiver.heard
    assert post.headers["x-pinecall-event"] == "monitor.fired"
    assert (fired[0].data["env"], json.loads(post.content)["env"]) == ("sandbox", "sandbox")


@postgres
async def test_a_call_whose_drift_cannot_be_counted_is_sealed_all_the_same(
    wired: Gateway, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="pinecall.gateway.ending.seal")
    context = a_call()
    await wired.logs.store.claim(context.call, AGENT, OURS.org, Claim(OURS, Versions()))
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    await sealed(wired.serving, served, SealCallRequest(usage=[], outcome="booked"))
    assert await wired.logs.store.sealed(context.call)
    assert "was not counted into its day's drift" in caplog.text, "OURS names no org row"


@postgres
async def test_a_seal_that_broke_after_its_summary_goes_on_from_the_score(wired: Gateway) -> None:
    context = a_call()
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    store = wired.logs.store
    first = SealCallRequest(usage=[], outcome="booked")
    await summed_up(wired.connections.pool, store, served.log, first)
    await sealed(wired.serving, served, SealCallRequest(usage=[], outcome="asked again"))
    whole = await store.whole(context.call)
    outcomes = [item.data["outcome"] for item in whole if item.type == "call.summary"]
    assert outcomes == ["booked"]
    assert whole[-1].type == "call.score"
    assert context.call not in wired.live.calls


@postgres
async def test_the_seal_prices_the_phone_leg_by_the_trunks_rate_and_never_names_the_number(
    wired: Gateway,
) -> None:
    pool = wired.connections.pool
    box = await catalog.providers(pool)
    rates = {**box.rates, "twilio-inbound/+1": Rate(minutes=0.0034)}
    await catalog.configure(pool, box.model_copy(update={"rates": rates}))
    context = a_call(channel="phone")
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    leg: JsonObject = {
        "sip.callID": "SCL_1",
        "sip.ruleID": "SDR_1",
        "sip.twilio.callSid": "CA_1",
        "sip.trunkPhoneNumber": "+15550100133",
    }
    joined: JsonObject = {"identity": "sip_caller", "kind": "caller", "attributes": leg}
    await served.log.append("participant.joined", joined)
    await served.log.append(
        "call.ended",
        {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 5.0, "duration_s": 30.0},
    )
    await sealed(wired.serving, served, SealCallRequest(usage=[], outcome="answered"))
    whole = await wired.logs.store.whole(context.call)
    summary = next(item for item in whole if item.type == "call.summary")
    cost = summary.data["cost"]
    assert isinstance(cost, dict)
    assert cost["rows"] == [
        {
            "provider": "twilio",
            "model": "twilio-inbound/+1",
            "unit": "minutes",
            "quantity": 1,
            "unit_price_usd": 0.0034,
            "usd": 0.0034,
        }
    ]
    assert "+15550100133" not in str(cost)


async def sealed_call(
    wired: Gateway,
    context: CallContext,
    *turns: tuple[str, JsonObject],
    declared: AgentConfig | None = None,
) -> list[Entry]:
    """A call of the agent as declared that said these turns, ended and sealed: its whole log."""
    scope = Scope(context.route.org, context.route.env)
    config = declared or AgentConfig(slug=AGENT)
    served = served_call(wired.serving, None, context, config, scope)
    for kind, data in turns:
        await served.log.append(kind, data)
    await served.log.append(
        "call.ended",
        {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 5.0, "duration_s": 30.0},
    )
    await sealed(wired.serving, served, SealCallRequest(usage=[], outcome="priced"))
    return await wired.logs.store.whole(context.call)


PRICED: tuple[tuple[str, JsonObject], ...] = (
    ("turn.user", {"speech_id": "sp_1", "text": "¿Cuánto cuesta?", "metrics": {}}),
    (
        "turn.agent",
        {"speech_id": "sp_1", "text": "Son 45 euros.", "interrupted": False, "metrics": {}},
    ),
)


GROUNDED_BROKE = (a_hold(), a_hold(), broken("60 €", 3), NOT_APPLIES, NOT_APPLIES)


NONE_OF_THE_LIBRARY = judges.Switched(tuple(library()), on=False, author="m_ana")


def verdicts_of(whole: list[Entry]) -> dict[str, str]:
    """Each judge's verdict on the sealed call, by name."""
    score = CallScore.model_validate(whole[-1].data)
    return {judgment.name: judgment.verdict for judgment in score.judges}


@postgres
async def test_a_box_that_names_no_judge_settles_the_gated_judges_and_skips_the_rest(
    wired: Gateway,
) -> None:
    whole = await sealed_call(wired, a_call(), *PRICED)
    score = CallScore.model_validate(whole[-1].data)
    assert verdicts_of(whole) == {
        "consent": "na",
        "disclosed": "skipped",
        "ended-well": "skipped",
        "grounded": "skipped",
        "honoured-stop": "skipped",
        "identified": "na",
        "promises": "skipped",
    }
    grounded = next(judgment for judgment in score.judges if judgment.name == "grounded")
    assert grounded.reason.startswith(NO_JUDGE)
    assert (score.passed, score.evals, score.judge_calls) == (None, 0, 0)


@postgres
async def test_a_box_that_names_a_judge_asks_it_on_its_own_key_and_prices_it(
    wired: Gateway,
) -> None:
    await catalog.configure(wired.connections.pool, judging(*GROUNDED_BROKE))
    whole = await sealed_call(wired, a_call(), *PRICED)
    score = CallScore.model_validate(whole[-1].data)
    assert verdicts_of(whole)["grounded"] == "broken"
    assert verdicts_of(whole)["promises"] == "na"
    assert score.passed is False
    # Five requests: three questions and two triggers; the triggers' no is not an eval.
    assert (score.judge_calls, score.evals) == (5, 3)
    assert score.judge_cost_usd is not None


@postgres
async def test_a_call_a_judge_broke_on_waits_in_the_inbox_and_one_that_held_does_not(
    wired: Gateway,
) -> None:
    pool = wired.connections.pool
    org = await orgs.create(pool, "clinica-norte", "Clinica Norte")
    await catalog.configure(pool, judging(*EVERY_HELD))
    await sealed_call(wired, a_call(Scope(org.id, "sandbox")), *PRICED)
    assert await dataset.listed(pool, org.id, AGENT) == [], "every judge held"
    await catalog.configure(pool, judging(*GROUNDED_BROKE))
    await sealed_call(wired, a_call(Scope(org.id, "sandbox")), *PRICED)
    (case,) = await dataset.listed(pool, org.id, AGENT, "pending")
    assert case.golden.input == ["¿Cuánto cuesta?"]
    assert case.golden.expect.grounded is True
    assert [judgment.judge for judgment in case.broke] == ["grounded"]
    assert case.name.startswith("grounded-cuanto-cuesta-")


@postgres
async def test_an_org_that_judges_nothing_at_hang_up_is_sealed_saying_so(wired: Gateway) -> None:
    org = await orgs.create(wired.connections.pool, "org-quiet", "Quiet")
    await orgs.set_judging(wired.connections.pool, org.id, on=False)
    context = a_call(Scope(org.id, "sandbox"))
    served = served_call(
        wired.serving, None, context, AgentConfig(slug=AGENT), Scope(org.id, "sandbox")
    )
    await sealed(wired.serving, served, SealCallRequest(usage=[], outcome="none"))
    whole = await wired.logs.store.whole(context.call)
    score = CallScore.model_validate(whole[-1].data)
    assert score.judges == []
    assert score.not_judged is not None
    assert "not judged at hang-up" in score.not_judged


@postgres
async def test_a_call_an_eval_run_opened_is_judged_by_the_run_and_not_again_at_hang_up(
    wired: Gateway,
) -> None:
    context = dataclasses.replace(a_call(), run="run_1")
    whole = await sealed_call(wired, context, *PRICED)
    score = CallScore.model_validate(whole[-1].data)
    assert (score.judges, score.not_judged) == ([], A_RUN_JUDGES_IT)


GREETED: tuple[tuple[str, JsonObject], ...] = (
    ("turn.user", {"speech_id": "sp_1", "text": "Hola", "metrics": {}}),
    ("turn.agent", {"speech_id": "sp_1", "text": "Buenas.", "interrupted": False, "metrics": {}}),
)


@postgres
async def test_the_seal_asks_the_judges_switched_on_and_the_agents_own_but_no_simulations_one(
    wired: Gateway,
) -> None:
    pool = wired.connections.pool
    chose: tuple[str, JsonObject] = (
        "submit_choice",
        {"choice": "positive", "reason": "it thanked", "positions": []},
    )
    await catalog.configure(pool, judging(chose, a_hold("it greeted")))
    org = await orgs.create(pool, "org-own", "Own")
    every = tuple(library())
    await judges.switch(
        pool, org.id, THE_ORGS_OWN, judges.Switched(every, on=False, author="m_ana")
    )
    await judges.switch(
        pool, org.id, AGENT, judges.Switched(("sentiment",), on=True, author="m_ana")
    )
    greets = JudgeSpec(name="greets", question="The agent greeted the caller.")
    rehearsed = JudgeSpec(name="rehearsed", question="q", on="simulations")
    for judge in (greets, rehearsed):
        await judges.put_judge(pool, org.id, AGENT, judge, author="m_ana")
    whole = await sealed_call(wired, a_call(Scope(org.id, "sandbox")), *GREETED)
    score = CallScore.model_validate(whole[-1].data)
    assert score.panel == ["sentiment", "greets"]
    sentiment, own = score.judges
    assert (sentiment.verdict, sentiment.choice) == ("classified", "positive")
    assert (own.verdict, own.reason) == ("held", "it greeted")
    assert (score.passed, score.judge_calls, score.evals) == (True, 2, 2)


@postgres
async def test_a_simulated_call_is_summed_up_as_one_and_meets_the_simulations_judges(
    wired: Gateway,
) -> None:
    pool = wired.connections.pool
    org = await orgs.create(pool, "org-rehearsed", "Rehearsed")
    every = tuple(library())
    await judges.switch(
        pool, org.id, THE_ORGS_OWN, judges.Switched(every, on=False, author="m_ana")
    )
    rehearsed = JudgeSpec(name="rehearsed", question="q", on="simulations")
    await judges.put_judge(pool, org.id, AGENT, rehearsed, author="m_ana")
    context = a_call(Scope(org.id, "sandbox"))
    started = {**a_start(context), "persona": "impatient"}
    whole = await sealed_call(wired, context, ("call.started", started), *GREETED)
    summary = next(item for item in whole if item.type == "call.summary")
    assert summary.data["simulated"] is True
    assert CallScore.model_validate(whole[-1].data).panel == ["rehearsed"]
    person = await sealed_call(wired, a_call(Scope(org.id, "sandbox")), *GREETED)
    assert next(item for item in person if item.type == "call.summary").data["simulated"] is False


@postgres
async def test_a_judge_on_the_orgs_own_key_answers_and_no_eval_of_it_is_billed(
    wired: Gateway,
) -> None:
    pool = wired.connections.pool
    await catalog.configure(pool, judging(*EVERY_HELD))
    org = await orgs.create(pool, "org-keyed", "Keyed")
    await vault.put_credentials(pool, wired.connections.vault, org.id, "acme", {"api_key": "k"})
    await orgs.set_judge_model(pool, org.id, ModelConfig(provider="acme", model="acme-1"))
    whole = await sealed_call(wired, a_call(Scope(org.id, "sandbox")), *PRICED)
    score = CallScore.model_validate(whole[-1].data)
    assert (score.own_key, score.evals, score.passed) == (True, 3, True)
    billed = usage_row(Metered(position=whole[-1].seq, org=org.id, entry=whole[-1]))
    assert (billed.used.evals, billed.used.judge_calls) == (0, 5)


@postgres
async def test_the_agents_judge_model_wins_over_the_orgs_and_the_orgs_over_the_platforms(
    wired: Gateway,
) -> None:
    pool = wired.connections.pool
    await catalog.configure(pool, judging(*EVERY_HELD))
    org = await orgs.create(pool, "org-picky", "Picky")
    where = Scope(org.id, "sandbox")
    platforms = await sealed_call(wired, a_call(where), *GREETED)
    await orgs.set_judge_model(pool, org.id, ModelConfig(provider="acme", model="acme-org"))
    orgs_own = await sealed_call(wired, a_call(where), *GREETED)
    picky = AgentConfig(slug=AGENT, judge=Model(provider="acme", model="acme-agent"))
    agents_own = await sealed_call(wired, a_call(where), *GREETED, declared=picky)
    judged_on = [
        CallScore.model_validate(whole[-1].data).judged_by
        for whole in (platforms, orgs_own, agents_own)
    ]
    assert [None if by is None else by.model for by in judged_on] == [
        None,
        "acme-org",
        "acme-agent",
    ]


STOPPED: tuple[tuple[str, JsonObject], ...] = (
    ("turn.user", {"speech_id": "sp_1", "text": "Please stop calling me.", "metrics": {}}),
    (
        "turn.agent",
        {"speech_id": "sp_1", "text": "Understood.", "interrupted": False, "metrics": {}},
    ),
)


@postgres
async def test_the_judges_are_told_the_orgs_name_its_opening_and_an_opt_out_on_the_call(
    wired: Gateway,
) -> None:
    pool = wired.connections.pool
    org = await orgs.create(pool, "org-lawful", "Lawful")
    where = Scope(org.id, "sandbox")
    listed = a_call(where, channel="phone")
    await consents.give(
        pool,
        where,
        listed.caller,
        Given("opt_out", "the caller asked", "agent:x", call=listed.call),
    )
    around = await surroundings_of(pool, org.id, listed.call, AgentConfig(slug=AGENT))
    assert (around.org, around.opted_out) == ("Lawful", True)
    assert around.disclosure is not None
    assert "Lawful" in around.disclosure
    other = await surroundings_of(pool, org.id, a_call(where).call, None)
    assert other.opted_out is False


@postgres
async def test_a_judge_that_reads_the_prompt_is_told_each_blocks_last_text(wired: Gateway) -> None:
    pool = wired.connections.pool
    org = await orgs.create(pool, "org-prompted", "Prompted")
    for text in ("You book visits.", "You book visits, briefly."):
        await wired.serving.prompts.keep(pool, org.id, text)
    context = a_call(Scope(org.id, "sandbox"))
    changed: list[tuple[str, JsonObject]] = [
        ("prompt.changed", {"name": "identity", "hash": block_hash(text), "chars": len(text)})
        for text in ("You book visits.", "You book visits, briefly.")
    ]
    unkept: tuple[str, JsonObject] = (
        "prompt.changed",
        {"name": "view", "hash": block_hash("never kept"), "chars": 10},
    )
    whole = await sealed_call(wired, context, *changed, unkept, *GREETED)
    assert await prompt_of(pool, org.id, whole) == "## identity\nYou book visits, briefly."


# ── the seal remembers ──


KEEPS = AgentConfig(slug=AGENT, memory=MemoryPolicy(remember=("preference",)))

FLAT = Embedding(vendor="acme", url="https://embed.test/v1", model="embed-1", shape="embeddings")

AN_ADD = '[{"op": "add", "text": "Prefiere las mañanas", "category": "preference"}]'

SAID: tuple[tuple[str, JsonObject], ...] = (
    ("turn.user", {"speech_id": "sp_1", "text": "Mejor por la mañana", "metrics": {}}),
    (
        "turn.agent",
        {"speech_id": "sp_1", "text": "Anotado.", "interrupted": False, "metrics": {}},
    ),
)


@pytest.fixture
async def remembering(wired: Gateway) -> Scope:
    """An org made, in the sandbox: memory is admitted per org, so the row has to exist."""
    org = await orgs.create(wired.connections.pool, "org-a", "Org A")
    return Scope(org.id, "sandbox")


@pytest.fixture
async def embedding(wired: Gateway, remembering: Scope) -> AsyncIterator[Gateway]:
    """The gateway embedding on a fake vendor, the model answering one add."""
    del remembering
    await catalog.configure(wired.connections.pool, configured(replies=[[AN_ADD]]))
    async with httpx.AsyncClient(transport=Embeddings().transport()) as http:
        yield dataclasses.replace(wired, embedder=Embedder(FLAT, "a-key", http))


async def hung_up(embedding: Gateway, scope: Scope, config: AgentConfig) -> list[Entry]:
    """A phone call of the agent that said its turns, ended and was sealed: its whole log."""
    context = a_call(scope, channel="phone")
    served = served_call(embedding.serving, None, context, config, scope)
    for kind, data in SAID:
        await served.log.append(kind, data)
    await served.log.append(
        "call.ended",
        {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 5.0, "duration_s": 30.0},
    )
    await sealed(embedding.serving, served, SealCallRequest(usage=[], outcome="noted"))
    return await embedding.logs.store.whole(context.call)


@postgres
async def test_a_hang_up_remembers_between_call_ended_and_call_summary(
    embedding: Gateway, remembering: Scope
) -> None:
    whole = await hung_up(embedding, remembering, KEEPS)
    kinds = [entry.type for entry in whole]
    assert kinds[-4:] == ["call.ended", "memory.ops", "call.summary", "call.score"]
    ops = whole[-3].data["ops"]
    assert isinstance(ops, list)
    op = ops[0]
    assert isinstance(op, dict)
    assert (op["op"], op["contact"]) == ("remember", "+59899123456")
    facts = op["facts"]
    assert isinstance(facts, list)
    assert len(facts) == 1
    kept = await memory.current(embedding.connections.pool, remembering, "+59899123456")
    assert [fact.text for fact in kept] == ["Prefiere las mañanas"]


@postgres
async def test_the_memory_models_tokens_are_the_calls_and_priced_with_it(
    embedding: Gateway, remembering: Scope
) -> None:
    whole = await hung_up(embedding, remembering, KEEPS)
    summary = next(entry for entry in whole if entry.type == "call.summary")
    usage = summary.data["usage"]
    assert isinstance(usage, list)
    assert [(row["model"], row["input_tokens"]) for row in usage if isinstance(row, dict)] == [
        ("acme-1", 20)
    ]
    cost = summary.data["cost"]
    assert isinstance(cost, dict)
    assert cost["usd"] == 0.00003


@postgres
async def test_an_agent_that_declared_no_memory_asks_nobody_at_hang_up(
    embedding: Gateway, remembering: Scope
) -> None:
    whole = await hung_up(embedding, remembering, AgentConfig(slug=AGENT))
    assert "memory.ops" not in [entry.type for entry in whole]
    assert await embedding.logs.store.sealed(whole[0].call or "")


@postgres
async def test_a_hang_up_at_the_fact_cap_writes_no_fact_and_asks_no_model(
    embedding: Gateway, remembering: Scope
) -> None:
    pool = embedding.connections.pool
    await admission.set_quotas(pool, remembering.org, remembering.env, Quotas(memory_facts=0))
    whole = await hung_up(embedding, remembering, KEEPS)
    op = next(entry for entry in whole if entry.type == "memory.ops").data["ops"]
    assert op == [{"op": "remember", "contact": "+59899123456", "facts": [], "took_ms": 0.0}]
    assert await memory.current(pool, remembering, "+59899123456") == []
    agents_log = await embedding.logs.store.whole(log_name(None, AGENT))
    assert "credits.exhausted" in [entry.type for entry in agents_log]


@postgres
async def test_a_rememberer_that_fails_is_an_entry_and_the_call_still_seals(
    embedding: Gateway, remembering: Scope
) -> None:
    down = Embeddings(script=[httpx.ConnectError("nothing listens")] * 4)
    async with httpx.AsyncClient(transport=down.transport()) as http:
        broken = dataclasses.replace(embedding, embedder=Embedder(FLAT, "a-key", http))
        whole = await hung_up(broken, remembering, KEEPS)
    kinds = [entry.type for entry in whole]
    assert kinds[-4:] == ["call.ended", "error", "call.summary", "call.score"]
    failed = whole[-3].data
    assert (failed["code"], failed["recoverable"]) == ("remember_failed", True)
    assert "not written into memory" in str(failed["message"])
    assert await embedding.logs.store.sealed(whole[0].call or "")


@postgres
async def test_a_rememberer_past_its_budget_is_an_entry_and_the_call_still_seals(
    embedding: Gateway, remembering: Scope
) -> None:
    settings = embedding.connections.settings.model_copy(update={"remember_budget_s": 1e-9})
    hurried = dataclasses.replace(
        embedding, connections=dataclasses.replace(embedding.connections, settings=settings)
    )
    whole = await hung_up(hurried, remembering, KEEPS)
    kinds = [entry.type for entry in whole]
    assert kinds[-4:] == ["call.ended", "error", "call.summary", "call.score"]
    assert whole[-3].data["code"] == "remember_failed"

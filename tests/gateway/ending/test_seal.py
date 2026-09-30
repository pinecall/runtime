"""Tests for the seal: the summary and its cost, the judges at hang-up, and what memory kept."""

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from dataclasses import replace

import httpx
import pytest

from pinecall.domain.agent import AgentConfig, AgentJudge, MemoryPolicy
from pinecall.domain.call import CallContext
from pinecall.domain.names import JsonObject
from pinecall.domain.org import Quotas
from pinecall.domain.scope import Scope
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import opened, served_call
from pinecall.gateway.ending.seal import A_RUN_JUDGES_IT, NO_JUDGE, sealed, summed_up
from pinecall.log.logs import log_name
from pinecall.providers import catalog
from pinecall.providers.catalog import Embedding, Judge, Rate
from pinecall.retrieval import memory
from pinecall.retrieval.embed import Embedder
from pinecall.tenancy import admission, consents, judges, orgs
from pinecall.tenancy.consents import Given
from pinecall.wire.frames import Entry
from pinecall.wire.metrics import LLMModelUsage
from pinecall.wire.rest.calls import SealCallRequest
from pinecall.wire.scores import CallScore
from tests.conftest import configured, postgres
from tests.fakes.embeddings import Embeddings
from tests.gateway.conftest import AGENT, OURS, a_call, a_start

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
        "sip.trunkPhoneNumber": "+13617334133",
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
    assert "+13617334133" not in str(cost)


async def sealed_call(
    wired: Gateway, context: CallContext, *turns: tuple[str, JsonObject]
) -> list[Entry]:
    """A call that said these turns, ended and sealed: its whole log."""
    scope = Scope(context.route.org, context.route.env)
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), scope)
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


@postgres
async def test_a_box_that_names_no_judge_still_judges_by_code_and_skips_the_model_ones(
    wired: Gateway,
) -> None:
    whole = await sealed_call(wired, a_call(), *PRICED)
    score = CallScore.model_validate(whole[-1].data)
    verdicts = {judgment.name: judgment.verdict for judgment in score.judges}
    assert verdicts == {
        "consent": "held",
        "grounded": "skipped",
        "promises": "held",
        "disclosed": "held",
        "honoured_stop": "held",
    }
    grounded = next(judgment for judgment in score.judges if judgment.name == "grounded")
    assert grounded.reason.startswith(NO_JUDGE)
    assert score.passed is True


@postgres
async def test_a_box_that_names_a_judge_asks_it_on_its_own_key_and_prices_it(
    wired: Gateway,
) -> None:
    verdict: dict[str, object] = {
        "name": "submit_verdict",
        "arguments": {"verdict": "fail", "reasoning": "60 €"},
    }
    judged_box = configured([[verdict]]).model_copy(
        update={"judge": Judge.model_validate({"llm": {"vendor": "acme"}, "ceiling_usd": 0.01})}
    )
    await catalog.configure(wired.connections.pool, judged_box)
    whole = await sealed_call(wired, a_call(), *PRICED)
    score = CallScore.model_validate(whole[-1].data)
    assert {judgment.name: judgment.verdict for judgment in score.judges}["grounded"] == "broken"
    assert score.passed is False
    assert score.judge_calls == 1
    assert score.judge_cost_usd is not None


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
async def test_the_seal_asks_the_agents_own_judges_and_leaves_a_simulations_one_out(
    wired: Gateway,
) -> None:
    pool = wired.connections.pool
    verdict: dict[str, object] = {
        "name": "submit_verdict",
        "arguments": {"verdict": "pass", "reasoning": "it greeted"},
    }
    judged_box = configured([[verdict]]).model_copy(
        update={"judge": Judge.model_validate({"llm": {"vendor": "acme"}, "ceiling_usd": 0.01})}
    )
    await catalog.configure(pool, judged_box)
    org = await orgs.create(pool, "org-own", "Own")
    greets = AgentJudge(name="greets", question="The agent greeted the caller.")
    rehearsed = AgentJudge(name="rehearsed", question="q", runs_on="simulations")
    for judge in (greets, rehearsed):
        await judges.put_judge(pool, org.id, AGENT, judge, author="m_ana")
    whole = await sealed_call(wired, a_call(Scope(org.id, "sandbox")), *GREETED)
    score = CallScore.model_validate(whole[-1].data)
    assert score.panel == [
        "consent",
        "grounded",
        "promises",
        "disclosed",
        "honoured_stop",
        "greets",
    ]
    own = next(judgment for judgment in score.judges if judgment.name == "greets")
    assert (own.verdict, own.reason) == ("held", "it greeted")
    assert score.judge_calls == 1


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


STOPPED: tuple[tuple[str, JsonObject], ...] = (
    ("turn.user", {"speech_id": "sp_1", "text": "Please stop calling me.", "metrics": {}}),
    (
        "turn.agent",
        {"speech_id": "sp_1", "text": "Understood.", "interrupted": False, "metrics": {}},
    ),
)

OPENING = "This is an automated assistant calling on behalf of Lawful."

NAMED: tuple[str, JsonObject] = (
    "turn.agent",
    {"speech_id": "sp_0", "text": OPENING, "interrupted": False, "metrics": {}},
)


@postgres
async def test_the_seal_judges_compliance_from_the_orgs_own_facts(wired: Gateway) -> None:
    pool = wired.connections.pool
    org = await orgs.create(pool, "org-lawful", "Lawful")
    where = Scope(org.id, "sandbox")
    outbound = replace(a_call(where, channel="phone"), direction="outbound")
    whole = await sealed_call(wired, outbound, ("call.started", a_start(outbound)), NAMED, *GREETED)
    score = CallScore.model_validate(whole[-1].data)
    verdicts = {judgment.name: judgment.verdict for judgment in score.judges}
    assert (verdicts["identified"], verdicts["disclosed"]) == ("held", "held")

    listed = a_call(where, channel="phone")
    await consents.give(
        pool,
        where,
        listed.caller,
        Given("opt_out", "the caller asked", "agent:x", call=listed.call),
    )
    honoured = await sealed_call(wired, listed, *STOPPED)
    ignored = await sealed_call(wired, a_call(where), *STOPPED)
    assert stop_verdict(honoured) == "held"
    assert stop_verdict(ignored) == "broken"


def stop_verdict(whole: list[Entry]) -> str:
    """What honoured_stop said of the sealed call."""
    score = CallScore.model_validate(whole[-1].data)
    return next(judgment.verdict for judgment in score.judges if judgment.name == "honoured_stop")


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

"""Tests for what the gateway keeps in memory, and for the one seal and the reaper."""

import asyncio
import dataclasses
from collections.abc import AsyncIterator
from datetime import date

import httpx
import pytest

from pinecall.domain.agent import AgentConfig, MemoryPolicy
from pinecall.domain.call import CallContext, Route, new_call_id
from pinecall.domain.names import JsonObject
from pinecall.domain.org import Quotas
from pinecall.domain.scope import Scope
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import (
    A_RUN_JUDGES_IT,
    NO_JUDGE,
    attach,
    handed_on,
    opened,
    reaped,
    sealed,
    served_call,
)
from pinecall.gateway._sockets import Sockets
from pinecall.log.logs import Logs, log_name, started_entry
from pinecall.log.store import Store
from pinecall.providers import catalog
from pinecall.providers.catalog import Embedding, Judge
from pinecall.retrieval import memory
from pinecall.retrieval.embed import Embedder
from pinecall.tenancy import admission, orgs
from pinecall.wire.events import CallScore
from pinecall.wire.frames import Command, Entry
from pinecall.wire.metrics import LLMModelUsage
from pinecall.wire.rest.calls import SealCallRequest
from tests.conftest import configured, postgres
from tests.fakes.embeddings import Embeddings
from tests.fakes.livekit import Server

AGENT = "agenda"
OURS = Scope("org_a", "sandbox")
ANA = Scope("org_a", "sandbox", "m_ana")
BEN = Scope("org_a", "sandbox", "m_ben")
PRODUCTION = Scope("org_a", "production")


def sockets_over(store: Store) -> Sockets:
    """A registry over the test's logs."""
    return Sockets(Logs(store))


async def holding(sockets: Sockets, app: str, scope: Scope, *, console: bool = False) -> None:
    """A socket holding the agent in the scope."""
    await sockets.register(app, scope, AGENT, sdk=None, takes_unclaimed=not console)


def a_call(scope: Scope = OURS, channel: str = "web") -> CallContext:
    """A call of the agent in the scope."""
    route = Route(
        org=scope.org,
        agent=AGENT,
        channel="phone" if channel == "phone" else "web",
        number="+59829001199" if channel == "phone" else None,
        env=scope.env,
    )
    return CallContext(
        call=new_call_id(),
        channel=route.channel,
        direction="inbound",
        caller="+59899123456",
        route=route,
        today=date(2026, 9, 28),
    )


# ── who holds the agent ──


@postgres
async def test_a_new_call_takes_the_newest_socket_and_skips_a_console_and_a_draining_one(
    store: Store,
) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_1", OURS)
    await holding(sockets, "app_2", OURS)
    await holding(sockets, "app_3", OURS, console=True)
    newest = sockets.serving(OURS, AGENT, None)
    assert newest is not None
    assert newest.owner == "app_2"
    sockets.drain("app_2", "sandbox", AGENT)
    taking = sockets.serving(OURS, AGENT, None)
    assert taking is not None
    assert taking.owner == "app_1"


@postgres
async def test_a_developer_holding_none_falls_back_to_the_orgs_own(store: Store) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_1", OURS)
    taking = sockets.serving(ANA, AGENT, None)
    assert taking is not None
    assert taking.scope == OURS


@postgres
async def test_a_ring_from_a_developers_own_phone_reaches_their_corner(store: Store) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_ana", ANA)
    await holding(sockets, "app_ben", BEN)
    sockets.calls_from("sandbox", "+59899000001", "m_ben")
    mine = sockets.taking(OURS, AGENT, "+59899000001")
    stranger = sockets.taking(OURS, AGENT, "+59899999999")
    assert mine is not None
    assert stranger is not None
    assert (mine.owner, stranger.owner) == ("app_ben", "app_ana")
    assert sockets.forget_calls_from("sandbox", "m_ben") == ["+59899000001"]


@postgres
async def test_a_socket_leaving_says_whether_anybody_is_left(store: Store) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_1", PRODUCTION)
    await holding(sockets, "app_2", PRODUCTION)
    await sockets.release("app_1")
    await sockets.release("app_2")
    detached = [
        item.data["left"]
        for item in await store.whole(f"@{AGENT}")
        if item.type == "agent.detached"
    ]
    assert detached == [False, True]


# ── the calls served ──


@postgres
async def test_a_call_served_to_a_socket_reaches_it_and_moves_when_the_socket_leaves(
    wired: Gateway,
) -> None:
    await holding(wired.sockets, "app_1", OURS)
    await holding(wired.sockets, "app_2", OURS)
    got: list[Entry] = []

    async def into_the_socket(entry: Entry) -> None:
        got.append(entry)

    wired.live.sockets["app_1"] = into_the_socket
    wired.live.sockets["app_2"] = into_the_socket
    context = a_call()
    served_call(wired.serving, "app_2", context, AgentConfig(slug=AGENT), OURS)
    served = served_call(wired.serving, "app_2", context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", _started(context))
    await served.log.append("custom", {"name": "x", "data": {}})
    await asyncio.sleep(0.05)
    assert [item.type for item in got] == ["call.started", "custom"]
    await wired.sockets.release("app_2")
    handed, parked = await handed_on(
        wired.live, wired.logs.store, wired.sockets, ["call_nobody", context.call]
    )
    assert (handed, parked) == (1, 0)
    await asyncio.sleep(0.05)
    assert got[-1].type == "call.attached"
    assert wired.live.calls[context.call].app == "app_1"


@postgres
async def test_attaching_names_the_start_the_state_and_the_seq(wired: Gateway) -> None:
    context = a_call()
    served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", _started(context))
    await served.log.append("state.changed", {"state": {"step": 2}, "changed": ["step"]})
    entry = await attach(wired.live, wired.logs.store, context.call, "app_9")
    assert entry is not None
    assert entry.data["state"] == {"step": 2}
    assert entry.data["seq"] == 2
    assert await attach(wired.live, wired.logs.store, context.call, "app_9") is None


@postgres
async def test_a_command_is_held_for_the_worker_of_its_agents_call_only(wired: Gateway) -> None:
    context = a_call()
    served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    text = Command(type="agent.say", agent=AGENT, call=context.call, data={"text": "hola"})
    other = Command(type="agent.say", agent="otra", call=context.call, data={"text": "hola"})
    assert wired.live.commanded(context.call, AGENT, text)
    assert not wired.live.commanded(context.call, "otra", other)
    assert wired.live.running("org_a") == 1


# ── the end ──


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
async def test_a_quiet_call_whose_room_has_no_agent_is_ended_as_drained_and_sealed(
    wired: Gateway,
) -> None:
    context = a_call(channel="phone")
    served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    server = Server()
    reaped_now = await reaped(wired.serving, server, 10_000.0)
    assert reaped_now == [context.call]
    kinds = [item.type for item in await wired.logs.store.whole(context.call)]
    assert kinds == ["call.ringing", "call.ended", "call.summary", "call.score"]
    await server.aclose()


@postgres
async def test_a_call_with_an_agent_still_in_its_room_is_left_alone(wired: Gateway) -> None:
    context = a_call(channel="phone")
    served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    server = Server()
    server.rooms.existing = {context.call: True}
    assert await reaped(wired.serving, server, 10_000.0) == []
    await server.aclose()


def _started(context: CallContext) -> JsonObject:
    return started_entry(context, context.route.number or AGENT, 1.0)


@postgres
async def test_a_call_that_only_just_went_quiet_or_ended_properly_is_never_reaped(
    wired: Gateway,
) -> None:
    quiet = a_call(channel="phone")
    over = a_call(channel="phone")
    for context in (quiet, over):
        served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
        served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
        await opened(served.log, context, AGENT)
    await sealed(
        wired.serving, wired.live.calls[over.call], SealCallRequest(usage=[], outcome="done")
    )
    server = Server()
    assert await reaped(wired.serving, server, 100.0) == []
    assert await reaped(wired.serving, server, 10_000.0) == [quiet.call]
    await server.aclose()


@postgres
async def test_a_worker_that_wrote_call_ended_and_died_is_finished_from_there(
    wired: Gateway,
) -> None:
    context = a_call(channel="phone")
    served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    await served.log.append(
        "call.ended",
        {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 5.0, "duration_s": 30.0},
    )
    server = Server()
    assert await reaped(wired.serving, server, 10_000.0) == [context.call]
    assert await reaped(wired.serving, server, 10_000.0) == []
    kinds = [item.type for item in await wired.logs.store.whole(context.call)]
    assert kinds == ["call.ringing", "call.ended", "call.summary", "call.score"]
    summary = next(
        item for item in await wired.logs.store.whole(context.call) if item.type == "call.summary"
    )
    assert summary.data["reason"] == "caller_hung_up"
    await server.aclose()


@postgres
async def test_a_whatsapp_thread_nobody_runs_waits_its_two_hours_for_the_contact(
    wired: Gateway,
) -> None:
    route = Route(
        org="org_a", agent=AGENT, channel="whatsapp", number="+59829001199", env="sandbox"
    )
    context = CallContext(
        call=new_call_id(),
        channel="whatsapp",
        direction="inbound",
        caller="+59899123456",
        route=route,
        today=date(2026, 9, 28),
    )
    served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", _started(context))
    wired.live.close(context.call)
    server = Server()
    assert await reaped(wired.serving, server, 1.0 + 10 * 60) == []
    assert await reaped(wired.serving, server, 1.0 + 3 * 60 * 60) == [context.call]
    await server.aclose()


@postgres
async def test_a_written_call_this_process_runs_is_left_to_end_itself(wired: Gateway) -> None:
    context = a_call()
    served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", _started(context))
    server = Server()
    assert await reaped(wired.serving, server, 10_000.0) == []
    wired.live.close(context.call)
    assert await reaped(wired.serving, server, 10_000.0) == [context.call]
    await server.aclose()


async def sealed_call(
    wired: Gateway, context: CallContext, *turns: tuple[str, JsonObject]
) -> list[Entry]:
    """A call that said these turns, ended and sealed: its whole log."""
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
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
    assert verdicts == {"consent": "held", "grounded": "skipped", "promises": "held"}
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

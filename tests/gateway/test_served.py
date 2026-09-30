"""Tests for the calls the gateway serves: who holds the agent, and the calls handed on."""

import asyncio

import pytest

from pinecall.domain.agent import AgentConfig
from pinecall.domain.org import Quotas
from pinecall.domain.scope import Scope
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import RUNNING_CHANNEL, opened, served_call
from pinecall.gateway._sockets import Sockets
from pinecall.gateway.api import calls
from pinecall.gateway.ending.reaper import let_go
from pinecall.log import openings
from pinecall.log.logs import Logs
from pinecall.log.queries import CallScope
from pinecall.log.store import Store
from pinecall.postgres.pool import Pool
from pinecall.session.call import ToolUse
from pinecall.tenancy import admission
from pinecall.wire.parts import ToolResult
from pinecall.wire.rest.calls import OpenCallRequest, SealCallRequest
from tests.conftest import AGENT as THE_KNOCKED_AGENT
from tests.conftest import Knocking, postgres
from tests.fleet.test_client import a_call as a_widget_call
from tests.gateway.conftest import AGENT, OURS, a_call
from tests.session.test_tools import went_out

ANA = Scope("org_a", "sandbox", "m_ana")
BEN = Scope("org_a", "sandbox", "m_ben")
PRODUCTION = Scope("org_a", "production")


def sockets_over(store: Store) -> Sockets:
    """A registry over the test's logs."""
    return Sockets(Logs(store))


async def holding(sockets: Sockets, app: str, scope: Scope, *, console: bool = False) -> None:
    """A socket holding the agent in the scope."""
    await sockets.register(app, scope, AGENT, sdk=None, takes_unclaimed=not console)


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
async def test_the_calls_an_org_runs_are_counted_in_their_own_world(wired: Gateway) -> None:
    served_call(wired.serving, None, a_call(), AgentConfig(slug=AGENT), OURS)
    served_call(wired.serving, None, a_call(ANA), AgentConfig(slug=AGENT), ANA)
    served_call(wired.serving, None, a_call(PRODUCTION), AgentConfig(slug=AGENT), PRODUCTION)
    assert wired.live.running("org_a", "sandbox") == 2
    assert wired.live.running("org_a", "production") == 1
    assert wired.live.running("org_b", "production") == 0


# A gateway that restarted serves the call again from the worker's word, with nothing in memory:
# the tool the worker asks again is answered from the log, never sent to the app a second time.
@postgres
async def test_a_tool_that_finished_before_a_restart_is_answered_from_the_log(
    wired: Gateway,
) -> None:
    context = a_call()
    config = AgentConfig(slug=AGENT)
    served = served_call(wired.serving, None, context, config, OURS)
    use = ToolUse("t1", "book", {})
    out = went_out(served.log)
    first = asyncio.create_task(served.tools.ran(use, None))
    await asyncio.wait_for(out.wait(), 5)
    assert served.tools.answered(ToolResult(call_id="t1", name="book", output="booked"))
    await first
    wired.live.close(context.call)
    wired.logs.forget(context.call)
    again = served_call(wired.serving, None, context, config, OURS)
    assert await again.tools.ran(use, None) == await first
    kinds = [entry.type for entry in await wired.logs.store.whole(context.call)]
    assert kinds == ["tool.call", "tool.result"]


# ── two gateways of one box: a call opened on one, served by the other's doors ──

ENDED = {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 10.0, "duration_s": 42.0}


def note(n: int) -> dict[str, object]:
    return {"type": "custom", "data": {"name": "note", "data": {"n": n}}}


@postgres
async def test_a_call_opened_on_one_gateway_is_written_and_sealed_through_the_other(
    knocking: Knocking, knocking_two: Knocking
) -> None:
    context = a_widget_call(knocking)
    opening = OpenCallRequest(agent=THE_KNOCKED_AGENT, context=context).written()
    async with (
        knocking.http(knocking.fleet["sandbox"]) as first,
        knocking_two.http(knocking.fleet["sandbox"]) as second,
    ):
        assert (await first.post("/v1/calls", json=opening)).is_success
        written = [
            await second.post(f"/v1/calls/{context.call}/events", json=note(1)),
            await first.post(f"/v1/calls/{context.call}/events", json=note(2)),
            await second.post(f"/v1/calls/{context.call}/events", json=note(3)),
        ]
        await second.post(
            f"/v1/calls/{context.call}/events", json={"type": "call.ended", "data": ENDED}
        )
        sealed = await second.post(
            f"/v1/calls/{context.call}/sealed",
            json=SealCallRequest(usage=[], outcome="booked").written(),
        )
        after = await first.post(f"/v1/calls/{context.call}/events", json=note(4))
    assert [answer.json()["seq"] for answer in written] == [2, 3, 4]
    assert sealed.status_code == 204
    kinds = [entry.type for entry in await knocking.gateway.logs.store.whole(context.call)]
    assert kinds[-2:] == ["call.summary", "call.score"]
    assert kinds.count("call.summary") == 1
    assert context.call not in knocking_two.gateway.live.calls
    # The opener still serves it until it hears it was sealed; then it neither counts nor answers.
    assert knocking.gateway.live.running(knocking.org.id, "sandbox") == 1
    assert await let_go(knocking.gateway.serving) == [context.call]
    assert knocking.gateway.live.running(knocking.org.id, "sandbox") == 0
    assert after.status_code in {404, 409}


# The first knock reads the call once; after that the door asks nothing of the store to know it.
@postgres
async def test_a_call_seen_once_is_known_without_asking_the_store_again(
    knocking: Knocking, knocking_two: Knocking, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = a_widget_call(knocking)
    reads: list[str] = []
    real = calls.queries.scope_of_call

    async def counted(pool: Pool, call: str) -> CallScope | None:
        reads.append(call)
        return await real(pool, call)

    async with (
        knocking.http(knocking.fleet["sandbox"]) as first,
        knocking_two.http(knocking.fleet["sandbox"]) as second,
    ):
        opening = OpenCallRequest(agent=THE_KNOCKED_AGENT, context=context).written()
        assert (await first.post("/v1/calls", json=opening)).is_success
        monkeypatch.setattr(calls.queries, "scope_of_call", counted)
        for n in range(5):
            answer = await second.post(f"/v1/calls/{context.call}/events", json=note(n))
            assert answer.is_success
    assert reads == [context.call]
    served = knocking_two.gateway.live.calls[context.call]
    assert (served.opened_here, served.app, served.config.slug) == (False, None, THE_KNOCKED_AGENT)
    assert knocking_two.gateway.live.running(knocking.org.id, "sandbox") == 0


# A call an older release opened kept no opening: the other gateway says so, and the worker's
# reopen fills it in, as it did when a gateway restarted.
@postgres
async def test_a_call_that_kept_no_opening_is_filled_in_by_the_workers_reopen(
    knocking: Knocking, knocking_two: Knocking
) -> None:
    context = a_widget_call(knocking)
    opening = OpenCallRequest(agent=THE_KNOCKED_AGENT, context=context).written()
    async with (
        knocking.http(knocking.fleet["sandbox"]) as first,
        knocking_two.http(knocking.fleet["sandbox"]) as second,
    ):
        assert (await first.post("/v1/calls", json=opening)).is_success
        async with knocking.gateway.connections.pool.connection() as connection:
            await connection.execute("delete from call_openings where call = %s", (context.call,))
        forgotten = await second.post(f"/v1/calls/{context.call}/events", json=note(1))
        reopened = await second.post(f"/v1/calls/{context.call}/reopened", json=opening)
        again = await second.post(f"/v1/calls/{context.call}/events", json=note(1))
    assert (forgotten.status_code, reopened.status_code, again.status_code) == (404, 204, 200)
    kept = await openings.opening_of(knocking.gateway.connections.pool, context.call)
    assert kept is not None
    assert kept.context == context


# Two knocks of one seal on two gateways at once: the lease lets one seal, the other waits for it.
@postgres
async def test_a_seal_asked_of_two_gateways_at_once_is_done_once(
    knocking: Knocking, knocking_two: Knocking
) -> None:
    context = a_widget_call(knocking)
    opening = OpenCallRequest(agent=THE_KNOCKED_AGENT, context=context).written()
    seal = SealCallRequest(usage=[], outcome="booked").written()
    path = f"/v1/calls/{context.call}"
    async with (
        knocking.http(knocking.fleet["sandbox"]) as first,
        knocking_two.http(knocking.fleet["sandbox"]) as second,
    ):
        assert (await first.post("/v1/calls", json=opening)).is_success
        await first.post(f"{path}/events", json={"type": "call.ended", "data": ENDED})
        answers = await asyncio.gather(
            first.post(f"{path}/sealed", json=seal), second.post(f"{path}/sealed", json=seal)
        )
    assert [answer.status_code for answer in answers] == [204, 204]
    kinds = [entry.type for entry in await knocking.gateway.logs.store.whole(context.call)]
    assert (kinds.count("call.summary"), kinds.count("call.score")) == (1, 1)
    for gateway in (knocking.gateway, knocking_two.gateway):
        assert context.call not in gateway.live.calls


@postgres
async def test_a_seal_that_broke_gives_its_lease_back_for_the_next_knock(wired: Gateway) -> None:
    context = a_call()
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    store = wired.logs.store
    assert await store.lease_seal(context.call, 60)
    assert not await store.lease_seal(context.call, 60)
    await store.release_seal(context.call)
    assert await store.lease_seal(context.call, 60)
    await store.seal(context.call)
    await store.release_seal(context.call)
    assert not await store.lease_seal(context.call, 60)


# The concurrent-calls quota is the box's: a call opened on one gateway counts on the other.
@postgres
async def test_an_orgs_calls_on_one_gateway_count_against_its_limit_on_the_other(
    knocking: Knocking, knocking_two: Knocking
) -> None:
    await admission.set_quotas(
        knocking.gateway.connections.pool, knocking.org.id, "sandbox", Quotas(concurrent_calls=1)
    )
    first_call, second_call = a_widget_call(knocking), a_widget_call(knocking)
    counted = knocking_two.gateway.live.counted
    counted.every_s = knocking.gateway.live.counted.every_s = 0.01
    shares = await knocking_two.gateway.connections.signal.subscribe(RUNNING_CHANNEL)
    async with (
        knocking.http(knocking.fleet["sandbox"]) as first,
        knocking_two.http(knocking.fleet["sandbox"]) as second,
    ):
        opening = OpenCallRequest(agent=THE_KNOCKED_AGENT, context=first_call).written()
        assert (await first.post("/v1/calls", json=opening)).is_success
        async with asyncio.timeout(2):
            while knocking_two.gateway.live.running(knocking.org.id, "sandbox") < 1:
                await anext(shares)
                await asyncio.sleep(0)
        refused = await second.post(
            "/v1/calls",
            json=OpenCallRequest(agent=THE_KNOCKED_AGENT, context=second_call).written(),
        )
    shares.close()
    assert refused.status_code == 429

"""Tests for the calls the gateway serves: who holds the agent, and the calls handed on."""

import asyncio

from pinecall.domain.agent import AgentConfig
from pinecall.domain.scope import Scope
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import attach, handed_on, served_call
from pinecall.gateway._sockets import Sockets
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.wire.frames import Command, Entry
from tests.conftest import postgres
from tests.gateway.conftest import AGENT, OURS, a_call, a_start

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
    served = served_call(wired.serving, "app_2", context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", a_start(context))
    await served.log.append("custom", {"name": "x", "data": {}})
    await asyncio.sleep(0.05)
    assert [item.type for item in got] == ["call.started", "custom"]
    await wired.sockets.release("app_2")
    handed, parked = await handed_on(wired.live, wired.sockets, ["call_nobody", context.call])
    assert (handed, parked) == (1, 0)
    await asyncio.sleep(0.05)
    assert got[-1].type == "call.attached"
    assert wired.live.calls[context.call].app == "app_1"


@postgres
async def test_attaching_names_the_start_the_state_and_the_seq(wired: Gateway) -> None:
    context = a_call()
    served = served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", a_start(context))
    await served.log.append("state.changed", {"state": {"step": 2}, "changed": ["step"]})
    entry = await attach(wired.live, context.call, "app_9")
    assert entry is not None
    assert entry.data["state"] == {"step": 2}
    assert entry.data["seq"] == 2
    assert await attach(wired.live, context.call, "app_9") is None


@postgres
async def test_a_command_is_held_for_the_worker_of_its_agents_call_only(wired: Gateway) -> None:
    context = a_call()
    served_call(wired.serving, None, context, AgentConfig(slug=AGENT), OURS)
    text = Command(type="agent.say", agent=AGENT, call=context.call, data={"text": "hola"})
    other = Command(type="agent.say", agent="otra", call=context.call, data={"text": "hola"})
    assert wired.live.commanded(context.call, AGENT, text)
    assert not wired.live.commanded(context.call, "otra", other)


@postgres
async def test_the_calls_an_org_runs_are_counted_in_their_own_world(wired: Gateway) -> None:
    served_call(wired.serving, None, a_call(), AgentConfig(slug=AGENT), OURS)
    served_call(wired.serving, None, a_call(ANA), AgentConfig(slug=AGENT), ANA)
    served_call(wired.serving, None, a_call(PRODUCTION), AgentConfig(slug=AGENT), PRODUCTION)
    assert wired.live.running("org_a", "sandbox") == 2
    assert wired.live.running("org_a", "production") == 1
    assert wired.live.running("org_b", "production") == 0

"""Tests for what the gateway keeps in memory, and for the one seal and the reaper."""

import asyncio
from datetime import date

import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.types import AgentConfig, CallContext, Corner, JsonObject, Route, new_call_id
from pinecall.gateway.deps import Wired
from pinecall.gateway.live import (
    Registry,
    attach,
    handed_on,
    opened,
    reaped,
    sealed,
    served_call,
    tokens_of,
)
from pinecall.log.log import Logs, started_entry
from pinecall.log.store import Store
from pinecall.wire.frames import Command, Entry
from pinecall.wire.metrics import LLMModelUsage
from pinecall.wire.rest import Sealing
from tests.conftest import postgres
from tests.fakes import Server

AGENT = "agenda"
OURS = Corner("org_a", "sandbox")
ANA = Corner("org_a", "sandbox", "m_ana")
BEN = Corner("org_a", "sandbox", "m_ben")
PRODUCTION = Corner("org_a", "production")


def the_registry(store: Store) -> Registry:
    """A registry over the test's logs."""
    return Registry(Logs(store))


async def held(registry: Registry, app: str, corner: Corner, *, console: bool = False) -> None:
    """A socket holding the agent in the corner."""
    await registry.register(app, corner, AGENT, sdk=None, takes_unclaimed=not console)


def a_call(corner: Corner = OURS, channel: str = "web") -> CallContext:
    """A call of the agent in the corner."""
    route = Route(
        org=corner.org,
        agent=AGENT,
        channel="phone" if channel == "phone" else "web",
        number="+59829001199" if channel == "phone" else None,
        env=corner.env,
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
async def test_a_production_register_is_kept_and_a_sandbox_one_is_forgettable(
    store: Store,
) -> None:
    registry = the_registry(store)
    sandbox = await registry.register("app_1", OURS, AGENT, sdk=None, takes_unclaimed=True)
    production = await registry.register("app_2", PRODUCTION, AGENT, sdk="ts", takes_unclaimed=True)
    assert (sandbox.ephemeral, production.ephemeral) == (True, False)
    assert production.data == {"routes": [], "app": "app_2", "sdk": "ts", "env": "production"}


@postgres
async def test_a_slug_another_org_holds_is_refused(store: Store) -> None:
    registry = the_registry(store)
    await held(registry, "app_1", OURS)
    with pytest.raises(DeclarationRefused, match="another org"):
        await held(registry, "app_2", Corner("org_b", "sandbox"))


@postgres
async def test_a_new_call_takes_the_newest_socket_and_skips_a_console_and_a_draining_one(
    store: Store,
) -> None:
    registry = the_registry(store)
    await held(registry, "app_1", OURS)
    await held(registry, "app_2", OURS)
    await held(registry, "app_3", OURS, console=True)
    newest = registry.serving(OURS, AGENT, None)
    assert newest is not None
    assert newest.owner == "app_2"
    registry.drain("app_2", "sandbox", AGENT)
    taking = registry.serving(OURS, AGENT, None)
    assert taking is not None
    assert taking.owner == "app_1"


@postgres
async def test_a_developer_holding_none_falls_back_to_the_orgs_own(store: Store) -> None:
    registry = the_registry(store)
    await held(registry, "app_1", OURS)
    taking = registry.serving(ANA, AGENT, None)
    assert taking is not None
    assert taking.corner == OURS


@postgres
async def test_the_first_corner_takes_the_line_and_a_later_one_claims_it(store: Store) -> None:
    registry = the_registry(store)
    await held(registry, "app_ana", ANA)
    await held(registry, "app_ben", BEN)
    assert registry.line_of("sandbox", AGENT) == "m_ana"
    registry.take_the_line(BEN, AGENT)
    assert registry.line_of("sandbox", AGENT) == "m_ben"
    assert registry.drop_the_line(BEN, AGENT)
    assert registry.line_of("sandbox", AGENT) == "m_ana"


@postgres
async def test_a_line_is_refused_to_a_corner_whose_app_would_not_pick_up(store: Store) -> None:
    registry = the_registry(store)
    await held(registry, "app_ana", ANA, console=True)
    with pytest.raises(DeclarationRefused, match="not held"):
        registry.take_the_line(ANA, AGENT)


@postgres
async def test_the_line_is_handed_on_when_the_terminal_holding_it_closes(store: Store) -> None:
    registry = the_registry(store)
    await held(registry, "app_ana", ANA)
    await held(registry, "app_ben", BEN)
    await registry.release("app_ana")
    assert registry.line_of("sandbox", AGENT) == "m_ben"
    await registry.release("app_ben")
    assert registry.line_of("sandbox", AGENT) is None


@postgres
async def test_a_ring_from_a_developers_own_phone_reaches_their_corner(store: Store) -> None:
    registry = the_registry(store)
    await held(registry, "app_ana", ANA)
    await held(registry, "app_ben", BEN)
    registry.calls_from("sandbox", "+59899000001", "m_ben")
    mine = registry.taking(OURS, AGENT, "+59899000001")
    stranger = registry.taking(OURS, AGENT, "+59899999999")
    assert mine is not None
    assert stranger is not None
    assert (mine.owner, stranger.owner) == ("app_ben", "app_ana")
    assert registry.forget_calls_from("sandbox", "m_ben") == ["+59899000001"]


@postgres
async def test_a_listing_is_one_row_per_slug_and_a_team_reader_sees_every_corner(
    store: Store,
) -> None:
    registry = the_registry(store)
    await held(registry, "app_ana", ANA)
    await held(registry, "app_ben", BEN)
    assert len(registry.holding(ANA, every_corner=False)) == 1
    assert len(registry.holding(ANA, every_corner=True)) == 2


@postgres
async def test_the_declaration_a_slug_alone_names_is_productions(store: Store) -> None:
    registry = the_registry(store)
    await held(registry, "app_1", OURS)
    await held(registry, "app_2", PRODUCTION)
    await registry.configure(
        "app_2", "production", AGENT, AgentConfig(slug=AGENT, language="en"), ["language"]
    )
    declared = registry.declared(AGENT)
    assert declared is not None
    assert declared.language == "en"


@postgres
async def test_a_socket_leaving_says_whether_anybody_is_left(store: Store) -> None:
    registry = the_registry(store)
    await held(registry, "app_1", PRODUCTION)
    await held(registry, "app_2", PRODUCTION)
    await registry.release("app_1")
    await registry.release("app_2")
    detached = [
        one.data["left"] for one in await store.whole(f"@{AGENT}") if one.type == "agent.detached"
    ]
    assert detached == [False, True]


# ── the calls served ──


@postgres
async def test_a_call_served_to_a_socket_reaches_it_and_moves_when_the_socket_leaves(
    box: Wired,
) -> None:
    await held(box.registry, "app_1", OURS)
    await held(box.registry, "app_2", OURS)
    got: list[Entry] = []

    async def into_the_socket(entry: Entry) -> None:
        got.append(entry)

    box.live.sockets["app_1"] = into_the_socket
    box.live.sockets["app_2"] = into_the_socket
    context = a_call()
    served = served_call(box.gated, "app_2", context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", _started(context))
    await served.log.append("custom", {"name": "x", "data": {}})
    await asyncio.sleep(0.05)
    assert [one.type for one in got] == ["call.started", "custom"]
    await box.registry.release("app_2")
    handed, parked = await handed_on(
        box.live, box.logs.store, box.registry, ["call_nobody", context.call]
    )
    assert (handed, parked) == (1, 0)
    await asyncio.sleep(0.05)
    assert got[-1].type == "call.attached"
    assert box.live.calls[context.call].app == "app_1"


@postgres
async def test_a_call_whose_socket_left_waits_parked_for_the_next(box: Wired) -> None:
    await held(box.registry, "app_1", OURS)
    context = a_call()
    served_call(box.gated, "app_1", context, AgentConfig(slug=AGENT), OURS)
    await box.registry.release("app_1")
    assert await handed_on(box.live, box.logs.store, box.registry, [context.call]) == (0, 1)
    assert box.live.parked(OURS, AGENT) == [context.call]


@postgres
async def test_attaching_names_the_start_the_state_and_the_seq(box: Wired) -> None:
    context = a_call()
    served = served_call(box.gated, None, context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", _started(context))
    await served.log.append("state.changed", {"state": {"step": 2}, "changed": ["step"]})
    entry = await attach(box.live, box.logs.store, context.call, "app_9")
    assert entry is not None
    assert entry.data["state"] == {"step": 2}
    assert entry.data["seq"] == 2
    assert await attach(box.live, box.logs.store, context.call, "app_9") is None


@postgres
async def test_a_command_is_held_for_the_worker_of_its_agents_call_only(box: Wired) -> None:
    context = a_call()
    served_call(box.gated, None, context, AgentConfig(slug=AGENT), OURS)
    said = Command(type="agent.say", agent=AGENT, call=context.call, data={"text": "hola"})
    other = Command(type="agent.say", agent="otra", call=context.call, data={"text": "hola"})
    assert box.live.commanded(context.call, AGENT, said)
    assert not box.live.commanded(context.call, "otra", other)
    assert box.live.running("org_a") == 1


# ── the end ──


@postgres
async def test_the_seal_writes_the_summary_the_score_and_lets_the_call_go(box: Wired) -> None:
    context = a_call()
    served = served_call(box.gated, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    await served.log.append(
        "call.ended",
        {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 5.0, "duration_s": 30.0},
    )
    used = LLMModelUsage(provider="acme", model="acme-1", input_tokens=1000, output_tokens=500)
    await sealed(box.gated, served, Sealing(usage=[used], outcome="booked"), lent=["acme"])
    whole = await box.logs.store.whole(context.call)
    summary = next(one for one in whole if one.type == "call.summary")
    assert (summary.data["reason"], summary.data["duration_s"], summary.data["outcome"]) == (
        "caller_hung_up",
        30.0,
        "booked",
    )
    cost = summary.data["cost"]
    assert isinstance(cost, dict)
    assert isinstance(cost["eur"], float)
    assert cost["eur"] > 0
    assert whole[-1].type == "call.score"
    assert await box.logs.store.sealed(context.call)
    assert context.call not in box.live.calls


def test_the_tokens_a_call_used_are_its_models_in_and_out() -> None:
    used = LLMModelUsage(provider="acme", model="acme-1", input_tokens=10, output_tokens=5)
    assert tokens_of([used]) == 15


@postgres
async def test_a_quiet_call_whose_room_has_no_agent_is_ended_as_drained_and_sealed(
    box: Wired,
) -> None:
    context = a_call(channel="phone")
    served = served_call(box.gated, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    server = Server()
    reaped_now = await reaped(box.gated, server, 10_000.0)
    assert reaped_now == [context.call]
    kinds = [one.type for one in await box.logs.store.whole(context.call)]
    assert kinds == ["call.ringing", "call.ended", "call.summary", "call.score"]
    await server.aclose()


@postgres
async def test_a_call_with_an_agent_still_in_its_room_is_left_alone(box: Wired) -> None:
    context = a_call(channel="phone")
    served = served_call(box.gated, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    server = Server()
    server.rooms.standing = {context.call: True}
    assert await reaped(box.gated, server, 10_000.0) == []
    await server.aclose()


@postgres
async def test_a_written_call_this_process_runs_is_left_to_end_itself(box: Wired) -> None:
    context = a_call()
    served = served_call(box.gated, None, context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", _started(context))
    server = Server()
    assert await reaped(box.gated, server, 10_000.0) == []
    box.live.close(context.call)
    assert await reaped(box.gated, server, 10_000.0) == [context.call]
    await server.aclose()


def _started(context: CallContext) -> JsonObject:
    return started_entry(context, context.route.number or AGENT, 1.0)


@postgres
async def test_a_call_that_only_just_went_quiet_or_ended_properly_is_never_reaped(
    box: Wired,
) -> None:
    quiet = a_call(channel="phone")
    over = a_call(channel="phone")
    for context in (quiet, over):
        served = served_call(box.gated, None, context, AgentConfig(slug=AGENT), OURS)
        await opened(served.log, context, AGENT)
    await sealed(box.gated, box.live.calls[over.call], Sealing(usage=[], outcome="done"))
    server = Server()
    assert await reaped(box.gated, server, 100.0) == []
    assert await reaped(box.gated, server, 10_000.0) == [quiet.call]
    await server.aclose()


@postgres
async def test_a_worker_that_wrote_call_ended_and_died_is_finished_from_there(
    box: Wired,
) -> None:
    context = a_call(channel="phone")
    served = served_call(box.gated, None, context, AgentConfig(slug=AGENT), OURS)
    await opened(served.log, context, AGENT)
    await served.log.append(
        "call.ended",
        {"reason": "caller_hung_up", "ended_by": "caller", "ended_at": 5.0, "duration_s": 30.0},
    )
    server = Server()
    assert await reaped(box.gated, server, 10_000.0) == [context.call]
    assert await reaped(box.gated, server, 10_000.0) == []
    kinds = [one.type for one in await box.logs.store.whole(context.call)]
    assert kinds == ["call.ringing", "call.ended", "call.summary", "call.score"]
    summary = next(
        one for one in await box.logs.store.whole(context.call) if one.type == "call.summary"
    )
    assert summary.data["reason"] == "caller_hung_up"
    await server.aclose()


@postgres
async def test_a_whatsapp_thread_nobody_runs_waits_its_two_hours_for_the_contact(
    box: Wired,
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
    served = served_call(box.gated, None, context, AgentConfig(slug=AGENT), OURS)
    await served.log.append("call.started", _started(context))
    box.live.close(context.call)
    server = Server()
    assert await reaped(box.gated, server, 1.0 + 10 * 60) == []
    assert await reaped(box.gated, server, 1.0 + 3 * 60 * 60) == [context.call]
    await server.aclose()

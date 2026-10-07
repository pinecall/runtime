"""Tests for the sockets of app sockets: who holds each agent, per scope and world."""

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import pytest

from pinecall.domain.agent import AgentConfig
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.scope import Scope
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._sockets import HELD_CHANNEL, Sockets
from pinecall.gateway.calls.binding import handed_on
from pinecall.gateway.calls.serving import served_call
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.process import shared
from pinecall.process.signal import LocalSignal
from pinecall.wire.commands import AgentRegister
from tests.conftest import postgres
from tests.gateway.test_served import (
    AGENT,
    ANA,
    BEN,
    OURS,
    PRODUCTION,
    a_call,
    holding,
    sockets_over,
)


@postgres
async def test_a_production_register_is_kept_and_a_sandbox_one_is_forgettable(
    store: Store,
) -> None:
    sockets_over(store)
    sockets = sockets_over(store)
    sandbox = await sockets.register("app_1", OURS, AGENT, AgentRegister(routes=[]))
    production = await sockets.register(
        "app_2", PRODUCTION, AGENT, AgentRegister(routes=[], sdk="ts")
    )
    assert (sandbox.ephemeral, production.ephemeral) == (True, False)
    assert production.data == {"routes": [], "app": "app_2", "sdk": "ts", "env": "production"}


@postgres
async def test_a_slug_another_org_holds_is_refused(store: Store) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_1", OURS)
    with pytest.raises(DeclarationRefused, match="another org"):
        await holding(sockets, "app_2", Scope("org_b", "sandbox"))


@postgres
async def test_the_first_corner_takes_the_line_and_a_later_one_claims_it(store: Store) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_ana", ANA)
    await holding(sockets, "app_ben", BEN)
    assert sockets.line_of("sandbox", AGENT) == "m_ana"
    sockets.take_the_line(BEN, AGENT)
    assert sockets.line_of("sandbox", AGENT) == "m_ben"
    assert sockets.drop_the_line(BEN, AGENT)
    assert sockets.line_of("sandbox", AGENT) == "m_ana"


@postgres
async def test_a_line_is_refused_to_a_corner_whose_app_would_not_pick_up(store: Store) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_ana", ANA, console=True)
    with pytest.raises(DeclarationRefused, match="not held"):
        sockets.take_the_line(ANA, AGENT)


@postgres
async def test_the_line_is_handed_on_when_the_terminal_holding_it_closes(store: Store) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_ana", ANA)
    await holding(sockets, "app_ben", BEN)
    await sockets.release("app_ana")
    assert sockets.line_of("sandbox", AGENT) == "m_ben"
    await sockets.release("app_ben")
    assert sockets.line_of("sandbox", AGENT) is None


@postgres
async def test_a_listing_is_one_row_per_slug_and_a_team_reader_sees_every_corner(
    store: Store,
) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_ana", ANA)
    await holding(sockets, "app_ben", BEN)
    assert len(sockets.holding(ANA, every_corner=False)) == 1
    assert len(sockets.holding(ANA, every_corner=True)) == 2


@postgres
async def test_the_console_is_answered_by_the_companion_and_not_by_the_newest_server(
    store: Store,
) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_companion", OURS, console=True, answers_dev=True)
    await holding(sockets, "app_server", OURS)
    answering = sockets.answering_dev(OURS, AGENT)
    assert answering is not None
    assert answering.owner == "app_companion"


@postgres
async def test_a_draining_companion_answers_the_console_no_more(store: Store) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_old", OURS, console=True, answers_dev=True)
    await holding(sockets, "app_new", OURS, console=True, answers_dev=True)
    sockets.drain("app_new", "sandbox", AGENT)
    answering = sockets.answering_dev(OURS, AGENT)
    assert answering is not None
    assert answering.owner == "app_old"


@postgres
async def test_servers_alone_leave_nobody_to_answer_the_console(store: Store) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_server", OURS)
    assert sockets.answering_dev(OURS, AGENT) is None


@postgres
async def test_the_declaration_a_slug_alone_names_is_productions(store: Store) -> None:
    sockets = sockets_over(store)
    await holding(sockets, "app_1", OURS)
    await holding(sockets, "app_2", PRODUCTION)
    await sockets.configure(
        "app_2", "production", AGENT, AgentConfig(slug=AGENT, language="en"), ["language"]
    )
    declared = sockets.declared(AGENT)
    assert declared is not None
    assert declared.language == "en"


@postgres
async def test_a_call_whose_socket_left_waits_parked_for_the_next(wired: Gateway) -> None:
    await holding(wired.sockets, "app_1", OURS)
    context = a_call()
    served_call(wired.serving, "app_1", context, AgentConfig(slug=AGENT), OURS)
    await wired.sockets.release("app_1")
    assert await handed_on(wired.live, wired.sockets, [context.call]) == (0, 1)
    assert wired.live.parked(OURS, AGENT) == [context.call]


@postgres
async def test_a_phone_is_the_person_who_verified_it_and_nobody_elses(store: Store) -> None:
    sockets = sockets_over(store)
    sockets.calls_from("production", "+13105550142", "m_ana")
    assert sockets.phone_of("production", " +13105550142 ") == "m_ana"
    assert sockets.phone_of("sandbox", "+13105550142") is None
    assert sockets.phone_of("production", "+12125550142") is None


# ── two gateways of one box: each holds its own sockets and reads every gateway's ──


@dataclass
class Two:
    """Two registries over one store and one signal, as two gateways of a box hold them."""

    first: Sockets
    second: Sockets
    signal: LocalSignal

    # Each share said is heard by every gateway in the order it was said: once the test hears
    # one and the loop turned, the gateways that heard it before have merged it.
    async def heard(self, until: Callable[[], bool]) -> None:
        """Wait, share by share, until what one gateway said stands on the other."""
        listening = await self.signal.subscribe(HELD_CHANNEL)
        # What was said before the test listened is merged once the loop turns.
        await asyncio.sleep(0)
        try:
            async with asyncio.timeout(2):
                while not until():
                    await anext(listening)
                    await asyncio.sleep(0)
        finally:
            listening.close()


@pytest.fixture
async def two(store: Store) -> AsyncIterator[Two]:
    """Two gateways' registries, started."""
    signal = LocalSignal()
    pair = Two(Sockets(Logs(store, signal)), Sockets(Logs(store, signal)), signal)
    await pair.first.start()
    await pair.second.start()
    yield pair
    await pair.first.close()
    await pair.second.close()


@postgres
async def test_a_socket_on_one_gateway_holds_the_agent_for_the_other(
    two: Two,
) -> None:
    first, second = two.first, two.second
    await holding(first, "app_ana", ANA)
    await two.heard(lambda: second.of(ANA, AGENT) is not None)
    found = second.serving(ANA, AGENT, None)
    assert found is not None
    assert (found.owner, second.line_of("sandbox", AGENT)) == ("app_ana", "m_ana")
    await holding(second, "app_ben", BEN)
    await two.heard(lambda: first.of(BEN, AGENT) is not None)
    assert [item.owner for item in first.waiting_for_the_line(ANA, AGENT)] == ["app_ben", "app_ana"]
    await first.release("app_ana")
    await two.heard(lambda: second.of(ANA, AGENT) is None)
    assert second.line_of("sandbox", AGENT) == "m_ben"


@postgres
async def test_a_line_and_a_phone_set_on_one_gateway_stand_on_the_other(
    two: Two,
) -> None:
    first, second = two.first, two.second
    await holding(first, "app_ana", ANA)
    await holding(second, "app_ben", BEN)
    await two.heard(lambda: first.of(BEN, AGENT) is not None and second.of(ANA, AGENT) is not None)
    second.take_the_line(BEN, AGENT)
    second.calls_from("sandbox", "+34600111222", "m_ben")
    await two.heard(lambda: first.line_of("sandbox", AGENT) == "m_ben")
    await two.heard(lambda: first.phone_of("sandbox", "+34600111222") == "m_ben")
    assert first.drop_the_line(BEN, AGENT)
    await two.heard(lambda: second.line_of("sandbox", AGENT) == "m_ana")
    assert first.line_of("sandbox", AGENT) == "m_ana"


# A gateway that died stops saying its share: the others forget its sockets once it is silent.
@postgres
async def test_a_gateway_gone_silent_takes_its_sockets_with_it(
    two: Two, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = two.first, two.second
    monkeypatch.setattr(shared, "SILENT_AT_MOST_S", 0.2)
    await second.close()
    second.shared.every_s = 0.05
    await second.start()
    await holding(first, "app_ana", ANA)
    await two.heard(lambda: second.of(ANA, AGENT) is not None)
    await first.close()
    await two.heard(lambda: second.of(ANA, AGENT) is None)

"""Tests for the sockets of app sockets: who holds each agent, per scope and world."""

import pytest

from pinecall.domain.agent import AgentConfig
from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.scope import Scope
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import (
    handed_on,
    served_call,
)
from pinecall.log.store import Store
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
    sandbox = await sockets.register("app_1", OURS, AGENT, sdk=None, takes_unclaimed=True)
    production = await sockets.register("app_2", PRODUCTION, AGENT, sdk="ts", takes_unclaimed=True)
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
    assert await handed_on(wired.live, wired.logs.store, wired.sockets, [context.call]) == (0, 1)
    assert wired.live.parked(OURS, AGENT) == [context.call]

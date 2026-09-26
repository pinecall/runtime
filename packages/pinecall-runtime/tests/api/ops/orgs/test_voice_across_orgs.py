"""One worker, every org: the fleet's key resolves a call by whose it is, a tenant's by itself."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date

import pytest

from pinecall.auth.keys_memory import MemoryKeys
from pinecall.live.registry import Registry
from pinecall.log.store import MemoryStore
from pinecall.routes.records_memory import MemoryRoutes
from pinecall.types import (
    DEFAULT_ORG,
    NOTHING_BROUGHT,
    PRODUCTION,
    SANDBOX,
    THE_FLEET,
    CallContext,
    Route,
)
from pinecall.worker.gateway_client import Gateway
from pinecall.worker.gateway_http import GatewayRefused
from pinecall_protocol import defs
from tests.api.conftest import A_RECORD, AGENT, over_the_asgi_app

pytestmark = pytest.mark.unit

# Two tenants on one box. The clinic is the suite's own org; the shop is the other one, and the
# worker's key belongs to neither: it is the box's, issued into org default with the fleet scope.
CLINICA = A_RECORD.org
TIENDA = "tienda"
SHOP = "tienda-sur"
A_NUMBER = "+34910000099"
# A developer of the clinic, holding their own copy of the agent in the sandbox.
CARLA = "m_carla"


@pytest.fixture
async def the_fleet(wired: None, keys: MemoryKeys) -> AsyncIterator[Gateway]:  # noqa: ARG001
    """The box's worker, knocking with the fleet's key."""
    issued = await keys.issue(
        DEFAULT_ORG, "the worker on this box", scopes=frozenset({THE_FLEET, "app", "calls"})
    )
    http = over_the_asgi_app(f"Bearer {issued.key}")
    yield Gateway(http)
    await http.aclose()


@pytest.fixture
async def a_clinic_worker(wired: None, keys: MemoryKeys) -> AsyncIterator[Gateway]:  # noqa: ARG001
    """The clinic's own worker, on a key of its own org."""
    issued = await keys.issue(CLINICA, "the clinic's worker", scopes=frozenset({"app", "calls"}))
    http = over_the_asgi_app(f"Bearer {issued.key}")
    yield Gateway(http)
    await http.aclose()


# The doors are the table's and the sockets are the registry's, which is the whole shape of this
# now: a number is a row an operator typed, and the widget is not a door — the clinic answers on
# the web in both corners and has no row at all.
async def two_orgs_holding(registry: Registry, table: MemoryRoutes | None = None) -> None:
    """The clinic on the web in production and in Carla's sandbox corner; the shop on a number."""
    if table is not None:
        await table.put(Route(org=TIENDA, agent=SHOP, channel="phone", number=A_NUMBER))
    await registry.register("app_clinic", CLINICA, PRODUCTION, AGENT)
    await registry.configure("app_clinic", PRODUCTION, AGENT, defs.AgentConfig(language="es-ES"))
    await registry.register("app_carla", CLINICA, SANDBOX, AGENT, holder=CARLA)
    await registry.configure("app_carla", SANDBOX, AGENT, defs.AgentConfig(language="es-UY"))
    await registry.register("app_shop", TIENDA, PRODUCTION, SHOP)
    await registry.configure("app_shop", PRODUCTION, SHOP, defs.AgentConfig(language="es-ES"))


def a_call(call: str, route: Route, holder: str | None = None) -> CallContext:
    return CallContext(
        call=call,
        channel=route.channel,
        direction="inbound",
        caller="visitor_1" if route.channel == "web" else "+34600000001",
        route=route,
        today=date(2026, 9, 16),
        holder=holder,
    )


async def test_the_fleet_reads_each_orgs_doors_by_naming_the_corner(
    the_fleet: Gateway, registry: Registry, routes: MemoryRoutes
) -> None:
    await two_orgs_holding(registry, routes)
    # The clinic answers on the web and the web is not a door: it has no row, in either corner.
    assert await the_fleet.routes(org=CLINICA, env=PRODUCTION) == ()
    assert await the_fleet.routes(org=CLINICA, env=SANDBOX, holder=CARLA) == ()
    assert await the_fleet.routes(org=TIENDA, env=PRODUCTION) == (
        Route(org=TIENDA, agent=SHOP, channel="phone", number=A_NUMBER),
    )


async def test_a_number_on_the_boxs_own_trunk_is_found_across_every_org(
    the_fleet: Gateway, registry: Registry, routes: MemoryRoutes
) -> None:
    """A phone call whose dispatch named no org: the number dialled says whose it is."""
    await two_orgs_holding(registry, routes)
    assert await the_fleet.routes(number=A_NUMBER, channel="phone") == (
        Route(org=TIENDA, agent=SHOP, channel="phone", number=A_NUMBER),
    )
    assert await the_fleet.routes(number="+34900000000", channel="phone") == ()


async def test_the_fleet_builds_each_orgs_session_from_that_orgs_declaration(
    the_fleet: Gateway, registry: Registry
) -> None:
    await two_orgs_holding(registry)
    assert (await the_fleet.agent(SHOP, org=TIENDA, env=PRODUCTION)).language == "es-ES"
    assert (
        await the_fleet.agent(AGENT, org=CLINICA, env=SANDBOX, holder=CARLA)
    ).language == "es-UY"
    assert await the_fleet.provider_keys(SHOP, org=TIENDA, env=PRODUCTION) == NOTHING_BROUGHT
    with pytest.raises(GatewayRefused, match="404"):
        await the_fleet.agent(SHOP, org=CLINICA, env=PRODUCTION)


async def test_the_fleet_opens_a_call_of_another_org_and_the_log_is_that_orgs(
    the_fleet: Gateway, registry: Registry, store: MemoryStore
) -> None:
    await two_orgs_holding(registry)
    route = Route(org=TIENDA, agent=SHOP, channel="phone", number=A_NUMBER)
    await the_fleet.opened(a_call("call_shop_1", route), SHOP)
    await the_fleet.append("call_shop_1", "agent.state", {"state": "thinking"}, True)
    assert await store.owner("call_shop_1", "") == TIENDA
    assert [entry.type for entry in await store.since("call_shop_1")] == [
        "call.ringing",
        "agent.state",
    ]
    await the_fleet.sealed("call_shop_1")


async def test_the_fleet_opens_a_sandbox_call_in_the_corner_the_dispatch_named(
    the_fleet: Gateway, registry: Registry, store: MemoryStore
) -> None:
    await two_orgs_holding(registry)
    route = Route(org=CLINICA, agent=AGENT, channel="web", env=SANDBOX)
    await the_fleet.opened(a_call("call_carla_1", route, holder=CARLA), AGENT)
    assert await store.owner("call_carla_1", "") == CLINICA
    await the_fleet.sealed("call_carla_1")


async def test_a_tenants_key_names_no_corner_but_its_own(
    a_clinic_worker: Gateway, registry: Registry, routes: MemoryRoutes
) -> None:
    """keys.md's sentence, held: a tenant that could ask for another org's routes could route a
    call into another org's agent. Only the fleet scope may, and the tenant has none."""
    await two_orgs_holding(registry, routes)
    assert await a_clinic_worker.routes() == ()
    for asking in (
        a_clinic_worker.routes(org=TIENDA),
        a_clinic_worker.routes(env=SANDBOX, holder=CARLA),
        a_clinic_worker.routes(number=A_NUMBER, channel="phone"),
        a_clinic_worker.agent(SHOP, org=TIENDA),
        a_clinic_worker.provider_keys(SHOP, org=TIENDA),
    ):
        with pytest.raises(GatewayRefused, match="403"):
            await asking
    with pytest.raises(GatewayRefused, match="403"):
        await a_clinic_worker.opened(
            a_call("call_shop_2", Route(org=TIENDA, agent=SHOP, channel="phone", number=A_NUMBER)),
            SHOP,
        )

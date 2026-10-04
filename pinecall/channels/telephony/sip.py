"""The SFU's side of a number: on its world's LiveKit, a trunk per fence and the world's rule."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import httpx
from livekit import api
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration
from livekit.protocol.sip import (
    CreateSIPDispatchRuleRequest,
    CreateSIPInboundTrunkRequest,
    DeleteSIPDispatchRuleRequest,
    DeleteSIPTrunkRequest,
    ListSIPDispatchRuleRequest,
    ListSIPInboundTrunkRequest,
    SIPDispatchRule,
    SIPDispatchRuleIndividual,
    SIPDispatchRuleInfo,
    SIPInboundTrunkInfo,
)

from pinecall.channels import rooms, routes
from pinecall.channels.rooms import Dispatch
from pinecall.channels.routes import RouteRecord
from pinecall.channels.telephony.carrier import Fence, declared_networks, fence_of, own_networks
from pinecall.domain.errors import NotAvailable
from pinecall.domain.names import ENVS, Env, other_world
from pinecall.fleet import worlds
from pinecall.process.connections import Connections
from pinecall.tenancy import carrier_networks
from pinecall.tenancy.carriers import Carrier, carriers_of

logger = logging.getLogger(__name__)


ROOM_PREFIX = "call-"


NO_DOMAIN = "this box has no PINECALL_DOMAIN: a carrier has nowhere to send a call"


REBUILT = "SIP rebuilt from the tables: %d numbers stand, %d orgs refused"


ROUTED_ORGS = """
SELECT DISTINCT org FROM routes WHERE channel = 'phone' AND number IS NOT NULL ORDER BY org
"""


@dataclass(frozen=True)
class WorldRule:
    """An org's world on the SFU: its rule, and the fleet the rule dispatches."""

    org: str
    env: Env
    fleet: str


@dataclass(frozen=True)
class Fencing:
    """What fences an org's numbers now: its carriers by id, and the networks approved."""

    carriers: dict[str, Carrier]
    approved: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class Rebuilt:
    """What the reconcile found: numbers existing on the SFU, orgs it could not rebuild."""

    numbers: int
    refused: list[str]


def domain_of(connections: Connections) -> str:
    """The box's name, or NotAvailable: a carrier has nowhere to send a call."""
    name = connections.settings.domain
    if not name:
        raise NotAvailable(NO_DOMAIN)
    return name


def sip_domain_of(connections: Connections, world: Env) -> str:
    """The name a carrier sends that world's calls to, or NotAvailable: it has none."""
    name = connections.settings.sip_name_of(world)
    if not name:
        raise NotAvailable(NO_DOMAIN)
    return name


# Both worlds hold the one client when the sandbox has no LiveKit of its own.
def elsewhere(connections: Connections, world: Env) -> api.LiveKitAPI | None:
    """The other world's LiveKit when it is not this world's too; None where both share one."""
    other = connections.servers[other_world(world)]
    return None if other is connections.servers[world] else other


def rule_name(org: str, env: Env) -> str:
    """The name of an org's dispatch rule in a world."""
    return f"{org}:{env}"


# An org id holds no colon, so `{org}` and `{org}:…` can never be another org's.
def belongs_to(name: str, org: str) -> bool:
    """Whether a trunk or rule name is the org's."""
    return name == org or name.startswith(f"{org}:")


async def trunks_admitting(server: api.LiveKitAPI, number: str) -> list[SIPInboundTrunkInfo]:
    """Every trunk on the SFU that lists the number."""
    listed = await server.sip.list_inbound_trunk(ListSIPInboundTrunkRequest(numbers=[number]))
    return [trunk for trunk in listed.items if number in trunk.numbers]


async def trunk_named(server: api.LiveKitAPI, name: str) -> SIPInboundTrunkInfo | None:
    """The trunk of that name, or None."""
    listed = await server.sip.list_inbound_trunk(ListSIPInboundTrunkRequest())
    return next((trunk for trunk in listed.items if trunk.name == name), None)


async def rule_named(server: api.LiveKitAPI, name: str) -> SIPDispatchRuleInfo | None:
    """The dispatch rule of that name, or None."""
    listed = await server.sip.list_dispatch_rule(ListSIPDispatchRuleRequest())
    return next((rule for rule in listed.items if rule.name == name), None)


async def admit(
    server: api.LiveKitAPI, fence: Fence, existing: SIPInboundTrunkInfo | None, number: str
) -> str:
    """Make the fence's trunk list the number, made or updated; its id."""
    if existing is None:
        info = SIPInboundTrunkInfo(
            name=fence.trunk,
            numbers=[number],
            allowed_addresses=list(fence.networks),
            auth_username=fence.username,
            auth_password=fence.password,
        )
        made = await server.sip.create_inbound_trunk(CreateSIPInboundTrunkRequest(trunk=info))
        return made.sip_trunk_id
    fenced = sorted(existing.allowed_addresses) == sorted(fence.networks)
    if number in existing.numbers and fenced and existing.auth_username == fence.username:
        return existing.sip_trunk_id
    existing.numbers[:] = sorted({*existing.numbers, number})
    existing.allowed_addresses[:] = list(fence.networks)
    existing.auth_username, existing.auth_password = fence.username, fence.password
    await server.sip.update_inbound_trunk(existing.sip_trunk_id, existing)
    return existing.sip_trunk_id


# A trunk or a rule that lists no number takes every number: one emptied is deleted, and so is
# a rule left with no trunk.
async def renumber_trunk(
    server: api.LiveKitAPI, org: str, trunk: SIPInboundTrunkInfo, numbers: list[str]
) -> None:
    """Leave the trunk these numbers; emptied, it goes, and so does a rule left with no trunk."""
    if numbers:
        await server.sip.update_inbound_trunk_fields(trunk.sip_trunk_id, numbers=numbers)
        return
    await server.sip.delete_trunk(DeleteSIPTrunkRequest(sip_trunk_id=trunk.sip_trunk_id))
    listed = await server.sip.list_dispatch_rule(ListSIPDispatchRuleRequest())
    for rule in listed.items:
        if not belongs_to(rule.name, org) or trunk.sip_trunk_id not in rule.trunk_ids:
            continue
        kept = [trunk_id for trunk_id in rule.trunk_ids if trunk_id != trunk.sip_trunk_id]
        if not kept:
            await renumber_rule(server, rule, [])
            continue
        rule.trunk_ids[:] = kept
        await server.sip.update_dispatch_rule(rule.sip_dispatch_rule_id, rule)


# One rule per world: its `numbers` are that world's, so a number changes world between two
# lists and never between trunks. livekit refuses two rules of one trunk listing one number.
async def rule_in(
    server: api.LiveKitAPI, world: WorldRule, trunks: Sequence[str], number: str
) -> None:
    """Have the world's rule list the number and the trunks, made or updated."""
    # A rule with no trunk dispatches every trunk's calls.
    if not trunks:
        return
    name, fleet = rule_name(world.org, world.env), world.fleet
    existing = await rule_named(server, name)
    metadata = rooms.written(Dispatch(org=world.org, env=world.env))
    agent_dispatch = RoomAgentDispatch(agent_name=fleet, metadata=metadata)
    if existing is None:
        info = SIPDispatchRuleInfo(
            name=name,
            trunk_ids=sorted(set(trunks)),
            numbers=[number],
            rule=SIPDispatchRule(
                dispatch_rule_individual=SIPDispatchRuleIndividual(room_prefix=ROOM_PREFIX)
            ),
            room_config=RoomConfiguration(agents=[agent_dispatch]),
        )
        await server.sip.create_dispatch_rule(CreateSIPDispatchRuleRequest(dispatch_rule=info))
        return
    wanted_trunks = sorted({*existing.trunk_ids, *trunks})
    fleets = [agent.agent_name for agent in existing.room_config.agents]
    if (
        number in existing.numbers
        and list(existing.trunk_ids) == wanted_trunks
        and fleets == [fleet]
    ):
        return
    existing.trunk_ids[:] = wanted_trunks
    existing.numbers[:] = sorted({*existing.numbers, number})
    existing.room_config.CopyFrom(RoomConfiguration(agents=[agent_dispatch]))
    await server.sip.update_dispatch_rule(existing.sip_dispatch_rule_id, existing)


async def renumber_rule(
    server: api.LiveKitAPI, rule: SIPDispatchRuleInfo, numbers: list[str]
) -> None:
    """Leave the rule these numbers; emptied, it goes."""
    if not numbers:
        gone = DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule.sip_dispatch_rule_id)
        await server.sip.delete_dispatch_rule(gone)
        return
    rule.numbers[:] = numbers
    await server.sip.update_dispatch_rule(rule.sip_dispatch_rule_id, rule)


# The rules are listed after the trunks: a trunk emptied takes a rule left with none with it.
async def let_go(server: api.LiveKitAPI, org: str, number: str) -> None:
    """Take the number off the org's trunks and off both of its worlds' rules on this LiveKit."""
    await _unlisted(server, org, number)
    names = {rule_name(org, env) for env in ENVS}
    listed = await server.sip.list_dispatch_rule(ListSIPDispatchRuleRequest())
    for rule in listed.items:
        if rule.name in names and number in rule.numbers:
            await renumber_rule(server, rule, [item for item in rule.numbers if item != number])


# LiveKit keeps its trunks and rules in Redis, which can be emptied; the tables are the truth.
# Nothing is deleted but a number the other world's LiveKit still lists, and the carrier's side is
# never touched.
async def rebuild(connections: Connections) -> Rebuilt:
    """Admit every routed phone number again on the SFU, with its fence and its world's rule."""
    async with connections.pool.connection() as connection:
        rows = await (await connection.execute(ROUTED_ORGS)).fetchall()
    readmitted, refused = 0, list[str]()
    for org in (str(row["org"]) for row in rows):
        try:
            readmitted += await readmit(connections, org)
        except (api.TwirpError, httpx.HTTPError):
            logger.warning("the SFU refused org %s's numbers: the others go on", org, exc_info=True)
            refused.append(org)
    logger.info(REBUILT, readmitted, len(refused))
    return Rebuilt(numbers=readmitted, refused=refused)


# After the operator approves or refuses a network: the org's trunks follow what is approved now.
async def readmit(connections: Connections, org: str) -> int:
    """Admit the org's phone numbers again with the fences they have now; how many stand."""
    fleets = await worlds.fleets(connections.pool)
    fencing = await fencing_of(connections, org)
    admitted = 0
    for record in await routes.phones_of(connections.pool, org):
        admitted += await readmitted(connections, record, fencing, fleets)
    return admitted


async def fencing_of(connections: Connections, org: str) -> Fencing:
    """The org's carriers and approved networks, which fence its numbers now."""
    carriers = await carriers_of(connections.pool, connections.vault, org)
    approved = await carrier_networks.approved(connections.pool, org)
    return Fencing(carriers={carrier.id: carrier for carrier in carriers}, approved=approved)


# A number lives on its world's LiveKit alone: the other world's, when it has its own, lets it go
# (a number moved, or one admitted there before the worlds had a LiveKit each). On its own LiveKit
# the other world's rule lets it go first: two rules of one trunk never list one number.
async def readmitted(
    connections: Connections, record: RouteRecord, fencing: Fencing, fleets: worlds.Fleets
) -> bool:
    """Admit the number on its world's LiveKit with the fence it has now; whether it stands."""
    org, number, env = record.route.org, str(record.route.number), record.route.env
    server, other = connections.servers[env], elsewhere(connections, env)
    if other is not None:
        await let_go(other, org, number)
    fence = fence_now(record, fencing.carriers.get(record.account or ""), fencing.approved)
    if fence is None:
        await _unlisted(server, org, number)
        return False
    trunk_id = await admit(server, fence, await trunk_named(server, fence.trunk), number)
    left = await rule_named(server, rule_name(org, other_world(env)))
    if left is not None and number in left.numbers:
        await renumber_rule(server, left, [listed for listed in left.numbers if listed != number])
    await rule_in(server, WorldRule(org, env, worlds.fleet_of(fleets, env)), [trunk_id], number)
    return True


def fence_now(
    record: RouteRecord, carrier: Carrier | None, approved: dict[str, tuple[str, ...]]
) -> Fence | None:
    """The fence a routed number has with what is approved now; None while nothing fences it."""
    number = str(record.route.number)
    declared = (record.networks or None) if carrier is None else declared_networks(carrier)
    source = number if carrier is None else carrier.id
    own, _ = own_networks(declared, approved.get(source, ()))
    return fence_of(record.route.org, number, carrier, record.via, own)


async def _unlisted(server: api.LiveKitAPI, org: str, number: str) -> None:
    for trunk in await trunks_admitting(server, number):
        if belongs_to(trunk.name, org):
            await renumber_trunk(server, org, trunk, [n for n in trunk.numbers if n != number])

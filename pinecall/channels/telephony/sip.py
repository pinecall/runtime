"""The SFU's side of a number: on its world's LiveKit, a trunk per fence and the world's rule."""

import asyncio
import logging
import socket
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
from pinecall.channels.telephony import hand_over
from pinecall.channels.telephony.carrier import Fence, declared_networks, fence_of, own_networks
from pinecall.domain.errors import NotAvailable
from pinecall.domain.names import ENVS, PRODUCTION, SANDBOX, Env, other_world
from pinecall.fleet import worlds
from pinecall.process.connections import NO_LIVEKIT, Connections
from pinecall.tenancy import carrier_networks
from pinecall.tenancy.carriers import Carrier, carriers_of
from pinecall.wire.rest.numbers import LegTrunk

logger = logging.getLogger(__name__)


ROOM_PREFIX = "call-"


NO_DOMAIN = "this box has no PINECALL_DOMAIN: a carrier has nowhere to send a call"


REBUILT = "SIP rebuilt from the tables: %d numbers stand, %d orgs refused, hand-overs admitted: %s"

NOT_WHOLE = "SIP not rebuilt whole (an org refused, or a LiveKit unreachable): again in %.0f s"

# A LiveKit still starting beside the gateway answers in seconds; one that is down, in minutes.
FIRST_WAIT_S = 2.0

LONGEST_WAIT_S = 60.0


NO_ADDRESS = "production's SIP name %s names no address: the sandbox admits no hand-over"


# How long the start waits for production's SIP name to say its address.
RESOLVED_WITHIN_S = 5.0


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
    # Whether the sandbox's own LiveKit admits the rings production hands over to it.
    hand_over: bool = False


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
    try:
        handing = await _hand_overs_admitted(connections)
    except (api.TwirpError, httpx.HTTPError):
        logger.warning("the sandbox's SFU refused the hand-over trunk", exc_info=True)
        handing = False
    logger.info(REBUILT, readmitted, len(refused), handing)
    return Rebuilt(numbers=readmitted, refused=refused, hand_over=handing)


# A gateway started beside a LiveKit that is still starting, or down, would otherwise leave every
# number of that world unadmitted until its own next start: it tries again until the rebuild is
# whole, waiting twice as long each time, a minute at most.
async def rebuilt_until_whole(connections: Connections, wait_s: float = FIRST_WAIT_S) -> Rebuilt:
    """The rebuild, again after a wait while an org is refused or a LiveKit cannot be reached."""
    while True:
        try:
            rebuilt = await rebuild(connections)
        except (api.TwirpError, httpx.HTTPError, OSError, TimeoutError):
            rebuilt = None
        if rebuilt is not None and not rebuilt.refused:
            return rebuilt
        logger.warning(NOT_WHOLE, wait_s)
        await asyncio.sleep(wait_s)
        wait_s = min(wait_s * 2, LONGEST_WAIT_S)


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


# Where each world has a LiveKit of its own, a developer's own phone at a production number is
# dialled from production's room to the sandbox's SIP (hand_over.py), with a pair drawn from
# LiveKit's secret; where both share one, the sandbox's fleet is sent into the room instead.
def hand_over_trunk(connections: Connections, caller: str) -> LegTrunk | None:
    """The trunk a ring is handed to the sandbox's SIP through; None where the worlds share one."""
    if elsewhere(connections, PRODUCTION) is None:
        return None
    return LegTrunk(
        hostname=sip_domain_of(connections, SANDBOX),
        transport="udp",
        username=hand_over.USERNAME,
        password=_hand_over_password(connections),
        shown=caller,
    )


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


# The trunk lists no number: it admits whatever production dials, from production's media
# address alone (the one its SIP name names, and livekit-sip sends from) and with the pair. Its
# rule sends each ring to the sandbox's fleet as one diverted from production.
async def _hand_overs_admitted(connections: Connections) -> bool:
    server = elsewhere(connections, PRODUCTION)
    named = connections.settings.sip_domain
    if server is None or not named:
        return False
    addresses = await _addresses_of(named)
    if not addresses:
        logger.warning(NO_ADDRESS, named)
        return False
    wanted = SIPInboundTrunkInfo(
        name=hand_over.HAND_OVER,
        allowed_addresses=addresses,
        auth_username=hand_over.USERNAME,
        auth_password=_hand_over_password(connections),
        headers_to_attributes=hand_over.ATTRIBUTE_OF,
    )
    trunk_id = await _hand_over_trunk_kept(server, wanted)
    fleet = worlds.fleet_of(await worlds.fleets(connections.pool), SANDBOX)
    await _hand_over_rule_kept(server, trunk_id, fleet)
    return True


def _hand_over_password(connections: Connections) -> str:
    secret = connections.settings.livekit_api_secret
    if not secret:
        raise NotAvailable(NO_LIVEKIT)
    return hand_over.password_of(secret)


async def _addresses_of(name: str) -> list[str]:
    try:
        async with asyncio.timeout(RESOLVED_WITHIN_S):
            found = await asyncio.get_running_loop().getaddrinfo(
                name, None, family=socket.AF_INET, type=socket.SOCK_DGRAM
            )
    except (OSError, TimeoutError):
        return []
    return sorted({str(address[4][0]) for address in found})


async def _hand_over_trunk_kept(server: api.LiveKitAPI, wanted: SIPInboundTrunkInfo) -> str:
    existing = await trunk_named(server, wanted.name)
    if existing is None:
        made = await server.sip.create_inbound_trunk(CreateSIPInboundTrunkRequest(trunk=wanted))
        return made.sip_trunk_id
    if (
        list(existing.allowed_addresses) == list(wanted.allowed_addresses)
        and existing.auth_username == wanted.auth_username
        and existing.auth_password == wanted.auth_password
        and dict(existing.headers_to_attributes) == dict(wanted.headers_to_attributes)
    ):
        return existing.sip_trunk_id
    await server.sip.update_inbound_trunk(existing.sip_trunk_id, wanted)
    return existing.sip_trunk_id


async def _hand_over_rule_kept(server: api.LiveKitAPI, trunk_id: str, fleet: str) -> None:
    metadata = rooms.written(Dispatch(env=SANDBOX, diverted_from=PRODUCTION))
    wanted = SIPDispatchRuleInfo(
        name=hand_over.HAND_OVER,
        trunk_ids=[trunk_id],
        rule=SIPDispatchRule(
            dispatch_rule_individual=SIPDispatchRuleIndividual(room_prefix=ROOM_PREFIX)
        ),
        room_config=RoomConfiguration(
            agents=[RoomAgentDispatch(agent_name=fleet, metadata=metadata)]
        ),
    )
    existing = await rule_named(server, hand_over.HAND_OVER)
    if existing is None:
        await server.sip.create_dispatch_rule(CreateSIPDispatchRuleRequest(dispatch_rule=wanted))
        return
    if list(existing.trunk_ids) == [trunk_id] and existing.room_config == wanted.room_config:
        return
    await server.sip.update_dispatch_rule(existing.sip_dispatch_rule_id, wanted)

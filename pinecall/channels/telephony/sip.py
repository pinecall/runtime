"""The SFU's side of a number: a trunk per fence, a dispatch rule per world, the rebuild."""

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
from psycopg.rows import DictRow

from pinecall.channels import rooms
from pinecall.channels.rooms import Dispatch
from pinecall.channels.telephony.twilio import TWILIO_SIGNALLING
from pinecall.domain.errors import NotAvailable
from pinecall.domain.names import Env
from pinecall.fleet import worlds
from pinecall.process.connections import Connections
from pinecall.tenancy.carriers import Carrier, SipPeer, TwilioAccount, WhatsappAccount, carriers_of

logger = logging.getLogger(__name__)


ROOM_PREFIX = "call-"


NO_DOMAIN = "this box has no PINECALL_DOMAIN: a carrier has nowhere to send a call"


REBUILT = "SIP rebuilt from the tables: %d numbers stand, %d orgs refused"


TO_REBUILD = """
SELECT org, number, env, account, networks FROM routes
WHERE channel = 'phone' AND number IS NOT NULL ORDER BY org, added_at, number
"""


@dataclass(frozen=True)
class Fence:
    """Who the SFU admits a number's INVITE from: one trunk per fence."""

    trunk: str
    networks: tuple[str, ...]
    username: str = ""
    password: str = ""


@dataclass(frozen=True)
class WorldRule:
    """An org's world on the SFU: its rule, and the fleet the rule dispatches."""

    org: str
    env: Env
    fleet: str


@dataclass(frozen=True)
class Rebuilt:
    """What the reconcile found: numbers existing on the SFU, orgs it could not rebuild."""

    numbers: int
    refused: list[str]


def domain_of(connections: Connections, world: Env) -> str:
    """The box's name in that world, or NotAvailable: a carrier has nowhere to send a call."""
    name = connections.settings.name_of(world)
    if not name:
        raise NotAvailable(NO_DOMAIN)
    return name


# The kind of an account is read here, where it decides the fence, and nowhere else on the SFU.
def fence_of(org: str, number: str, carrier: Carrier | None, networks: tuple[str, ...]) -> Fence:
    """Who the SFU admits the number from: the account's networks, or the ones the org gave."""
    if carrier is None:
        if networks:
            return Fence(trunk=f"{org}:{number}", networks=networks)
        return Fence(trunk=org, networks=TWILIO_SIGNALLING)
    match carrier.account:
        case TwilioAccount() | WhatsappAccount():
            return Fence(trunk=org, networks=TWILIO_SIGNALLING)
        case SipPeer():
            peer = carrier.account
            return Fence(
                trunk=f"{org}:{peer.username}",
                networks=tuple(peer.addresses),
                username=peer.username,
                password=peer.password,
            )


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


# LiveKit keeps its trunks and rules in Redis, which can be emptied; the tables are the truth.
# Nothing is deleted, and the carrier's side is never touched.
async def rebuild(connections: Connections) -> Rebuilt:
    """Admit every routed phone number again on the SFU, with its fence and its world's rule."""
    async with connections.pool.connection() as connection:
        rows = await (await connection.execute(TO_REBUILD)).fetchall()
    fleets = await worlds.fleets(connections.pool)
    readmitted, refused = 0, list[str]()
    for org in dict.fromkeys(str(row["org"]) for row in rows):
        try:
            carriers = {
                carrier.id: carrier
                for carrier in await carriers_of(connections.pool, connections.vault, org)
            }
            for row in (row for row in rows if row["org"] == org):
                await _readmit(connections, row, carriers.get(row["account"] or ""), fleets)
                readmitted += 1
        except (api.TwirpError, httpx.HTTPError):
            logger.warning("the SFU refused org %s's numbers: the others go on", org, exc_info=True)
            refused.append(org)
    logger.info(REBUILT, readmitted, len(refused))
    return Rebuilt(numbers=readmitted, refused=refused)


async def _readmit(
    connections: Connections, row: DictRow, carrier: Carrier | None, fleets: worlds.Fleets
) -> None:
    org, number, env = str(row["org"]), str(row["number"]), row["env"]
    fence = fence_of(org, number, carrier, tuple(row["networks"] or ()))
    trunk = await trunk_named(connections.server, fence.trunk)
    trunk_id = await admit(connections.server, fence, trunk, number)
    world = WorldRule(org, env, worlds.fleet_of(fleets, env))
    await rule_in(connections.server, world, [trunk_id], number)

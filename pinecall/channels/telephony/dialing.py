"""Dialling out: an account made dialable, the guards and their ledger, a call placed, a leg."""

import logging
import secrets
import time
from dataclasses import dataclass, replace
from datetime import date, datetime

from livekit import api
from livekit.protocol.sip import (
    SIPOutboundConfig,
    SIPTransport,
)
from psycopg.rows import DictRow

from pinecall.channels import rooms, routes
from pinecall.channels.rooms import Dialling, Dispatch
from pinecall.channels.telephony.sip import domain_of
from pinecall.channels.telephony.twilio import (
    TERMINATION_SUFFIX,
    Twilio,
    TwilioTrunk,
    origination_uri,
    termination_host,
    twilio_of,
)
from pinecall.domain.call import CallContext, new_call_id
from pinecall.domain.errors import (
    Conflict,
    DeclarationRefused,
    NotFound,
    UpstreamFailed,
)
from pinecall.domain.names import Env
from pinecall.domain.scope import Scope
from pinecall.fleet import worlds
from pinecall.log.logs import Logs, arrival_entry
from pinecall.log.store import Claim
from pinecall.postgres.pool import Pool
from pinecall.process.connections import Connections
from pinecall.tenancy import admission
from pinecall.tenancy.carriers import (
    Carrier,
    SipPeer,
    Termination,
    Transport,
    TwilioAccount,
    WhatsappAccount,
    carrier_named,
    seal_carrier,
)
from pinecall.tenancy.dial_policy import Dial, Guards, guard_dial, guard_second_leg, guards_of
from pinecall.wire.events import CallEnded
from pinecall.wire.rest.numbers import Consent, LegTrunk

logger = logging.getLogger(__name__)


NO_PHONE_DOOR = "agent {agent} answers at no phone number in the {env}: a call back shows one"


NOT_OUR_NUMBER = "{shown} is not a number agent {agent} answers at in the {env}"


DIALS_THROUGH_NOTHING = (
    "{shown} was hooked by the org or bought by the box: it dials through no account of the org"
)


NOT_PROVISIONED = (
    "the org's account {account} cannot place a call yet: POST /v1/carrier/outbound provisions it"
)


NO_OUTBOUND_HOST = (
    "SIP peer {account} declared no outbound_host: the networks a peer calls FROM are not an "
    "address it takes calls AT"
)


CREDENTIALS_LOST = (
    "Twilio account {account} holds the credential {username} on list {name}, whose password "
    "this box no longer has: delete that credential in Twilio's console and provision again"
)


DID_NOT_DIAL = "the media plane refused the call: {message}"


CALLING_FROM = """
SELECT number, account FROM routes
WHERE org = %(org)s AND env = %(env)s AND channel = 'phone' ORDER BY added_at, number
"""


TRANSPORTS: dict[Transport, SIPTransport] = {
    "auto": SIPTransport.SIP_TRANSPORT_AUTO,
    "udp": SIPTransport.SIP_TRANSPORT_UDP,
    "tcp": SIPTransport.SIP_TRANSPORT_TCP,
    "tls": SIPTransport.SIP_TRANSPORT_TLS,
}


@dataclass(frozen=True)
class OutboundReadiness:
    """Whether the org can place a call through an account, and what is still missing."""

    ready: bool
    kind: str | None
    from_numbers: list[str]
    steps_missing: list[str]
    guards: Guards


@dataclass(frozen=True)
class Provisioned:
    """What provisioning did, or would do."""

    steps: list[str]
    dry_run: bool
    ready: bool
    trunk: str | None = None
    address: str | None = None


@dataclass(frozen=True)
class TwilioSurvey:
    """What the account holds today: the trunk, its termination host, the list, the credential."""

    twilio: Twilio
    trunk: TwilioTrunk | None
    host: str
    list_name: str
    listed: str | None
    username: str
    # Whether the list already holds the org's credential; else one is made and sealed.
    holds: bool
    on_trunk: bool


@dataclass(frozen=True)
class OutboundSurvey:
    """How an account stands before anything is written: the steps read, and Twilio's own."""

    steps: list[str]
    address: str | None = None
    twilio: TwilioSurvey | None = None


@dataclass(frozen=True)
class Placement:
    """A call asked for: scope, agent, the far end, the number shown, who asked, when."""

    scope: Scope
    agent: str
    to: str
    shown: str | None
    asked_by: str
    today: date
    at: datetime
    # The far end is the asker's own verified phone: a test, held to no caller's hours.
    own_phone: bool = False
    consent: Consent | None = None


@dataclass(frozen=True)
class PlacedCall:
    """The call a dial became, before the far end has heard anything ring."""

    call: str
    to: str
    shown: str


async def outbound_readiness(
    connections: Connections, scope: Scope, account: str | None = None
) -> OutboundReadiness:
    """Whether the org can dial out in the world, one sentence per thing missing, in order."""
    guards = await guards_of(connections.pool, scope.org)
    numbers = [str(row["number"]) for row in await _calling_from(connections.pool, scope)]
    try:
        carrier = await carrier_named(connections.pool, connections.vault, scope.org, account)
    except NotFound as missing:
        return OutboundReadiness(
            ready=False,
            kind=None,
            from_numbers=numbers,
            steps_missing=[str(missing)],
            guards=guards,
        )
    missing = _missing_to_dial(carrier)
    if not numbers:
        missing.append(f"the org answers at no phone number in the {scope.env}: a call shows one")
    return OutboundReadiness(
        ready=not missing,
        kind=carrier.account.kind,
        from_numbers=numbers,
        steps_missing=missing,
        guards=guards,
    )


async def plan_outbound(
    connections: Connections, org: str, world: Env, account: str | None = None
) -> Provisioned:
    """What provisioning would write, and nothing written."""
    carrier = await carrier_named(connections.pool, connections.vault, org, account)
    survey = await _surveyed(connections, carrier, world)
    return Provisioned(
        steps=survey.steps, dry_run=True, ready=survey.twilio is None, address=survey.address
    )


async def provision_outbound(
    connections: Connections, org: str, world: Env, account: str | None = None
) -> Provisioned:
    """Make the account dialable: Twilio's termination and a credential; a peer needs nothing."""
    carrier = await carrier_named(connections.pool, connections.vault, org, account)
    survey = await _surveyed(connections, carrier, world)
    if survey.twilio is None:
        return Provisioned(steps=survey.steps, dry_run=False, ready=True, address=survey.address)
    trunk = await _twilio_provisioned(connections, carrier, survey.twilio, world)
    return Provisioned(
        steps=survey.steps, dry_run=False, ready=True, trunk=trunk, address=survey.address
    )


async def place_call(
    connections: Connections, logs: Logs, placement: Placement, *, running: int
) -> PlacedCall:
    """Place the call: number shown, account, guards, minutes, the log, then the dispatch."""
    scope = placement.scope
    doors = [
        route
        for route in await routes.of_org(connections.pool, scope.org, scope.env)
        if route.agent == placement.agent and route.channel == "phone"
    ]
    if not doors:
        raise NotFound(NO_PHONE_DOOR.format(agent=placement.agent, env=scope.env))
    shown = placement.shown or str(doors[0].number)
    route = next((door for door in doors if door.number == shown), None)
    if route is None:
        raise DeclarationRefused(
            NOT_OUR_NUMBER.format(shown=shown, agent=placement.agent, env=scope.env)
        )
    carrier = await _dials_through(connections, scope, shown)
    call = new_call_id()
    dial = Dial(
        scope,
        placement.agent,
        placement.to,
        shown,
        placement.asked_by,
        call,
        placement.at,
        own_phone=placement.own_phone,
        consent=placement.consent,
    )
    guards = await guard_dial(connections.pool, dial)
    await admission.admit_call(connections.pool, scope.org, scope.env, running=running)
    context = CallContext(
        call=call,
        channel="phone",
        direction="outbound",
        caller=placement.to,
        route=route,
        today=placement.today,
        holder=scope.holder or None,
    )
    await logs.store.claim(call, placement.agent, scope.org, Claim(scope))
    log = logs.writing(call, placement.agent)
    kind, data = arrival_entry(context, shown, asked_by=placement.asked_by)
    await log.append(kind, data)
    fleets = await worlds.fleets(connections.pool)
    carried = Dispatch(
        agent=placement.agent,
        org=scope.org,
        env=scope.env,
        holder=scope.holder or None,
        direction="outbound",
        caller=placement.to,
        dial=Dialling(
            trunk=carrier.id, to=placement.to, shown=shown, max_duration_s=guards.max_duration_s
        ),
    )
    try:
        await rooms.dispatched(
            connections.server, call, worlds.fleet_of(fleets, scope.env), carried
        )
    except api.TwirpError as refused:
        ended = CallEnded(
            reason="dial_failed", ended_by="platform", ended_at=time.time(), duration_s=0.0
        )
        await log.append("call.ended", ended.written())
        await log.seal()
        logs.forget(call)
        raise UpstreamFailed(DID_NOT_DIAL.format(message=refused.message)) from refused
    return PlacedCall(call=call, to=placement.to, shown=shown)


async def leg_trunk(connections: Connections, dial: Dial) -> LegTrunk:
    """How a leg of a live call is dialled, after its guards: the account of the number shown."""
    await guard_second_leg(connections.pool, dial)
    scope = dial.scope
    shown = dial.shown
    if shown is None:
        doors = [
            route
            for route in await routes.of_org(connections.pool, scope.org, scope.env)
            if route.agent == dial.agent and route.channel == "phone"
        ]
        if not doors:
            raise NotFound(NO_PHONE_DOOR.format(agent=dial.agent, env=scope.env))
        shown = str(doors[0].number)
    return _dialled_through(await _dials_through(connections, scope, shown), shown)


def sip_config(leg: LegTrunk) -> SIPOutboundConfig:
    """The inline trunk livekit dials a leg through."""
    return SIPOutboundConfig(
        hostname=leg.hostname,
        transport=TRANSPORTS[leg.transport],
        auth_username=leg.username,
        auth_password=leg.password,
    )


# The kind decides how a leg is dialled out, here and in `_dialled_through`.
def _missing_to_dial(carrier: Carrier) -> list[str]:
    match carrier.account:
        case TwilioAccount():
            return [] if carrier.outbound else [NOT_PROVISIONED.format(account=carrier.id)]
        case SipPeer():
            return (
                []
                if carrier.account.outbound_host
                else [NO_OUTBOUND_HOST.format(account=carrier.id)]
            )
        case WhatsappAccount():
            return [f"{carrier.id} is a WhatsApp number: it places no call"]


# This box makes nothing on somebody else's switch: a peer is dialled where it said.
async def _surveyed(connections: Connections, carrier: Carrier, world: Env) -> OutboundSurvey:
    match carrier.account:
        case TwilioAccount():
            return await _twilio_surveyed(connections, carrier, carrier.account, world)
        case SipPeer() if carrier.account.outbound_host:
            peer = carrier.account
            where = f"{peer.outbound_host} over {peer.outbound_transport}"
            return OutboundSurvey(
                steps=[f"SIP peer: dialled at {where}: stands"], address=peer.outbound_host
            )
        case SipPeer() | WhatsappAccount():
            raise Conflict(_missing_to_dial(carrier)[0])


# Read before anything is written, so a plan and a provisioning see the same account.
async def _twilio_surveyed(
    connections: Connections, carrier: Carrier, account: TwilioAccount, world: Env
) -> OutboundSurvey:
    domain = domain_of(connections, world)
    twilio = twilio_of(connections.http, account)
    trunk = await twilio.trunk_pointing_at(origination_uri(domain))
    host = termination_host(domain, account.account_sid)
    # One list per trunk, as the trunk is per account and box; one credential per org on it,
    # so two orgs that brought the same account each dial with their own.
    name = host.removesuffix(TERMINATION_SUFFIX)
    username = f"pinecall-{carrier.org}".replace("_", "-")
    listed = await twilio.credential_list(name)
    holds = listed is not None and username in await twilio.usernames_in(listed)
    if holds and carrier.outbound is None:
        raise Conflict(
            CREDENTIALS_LOST.format(account=account.account_sid, username=username, name=name)
        )
    on_trunk = (
        trunk is not None and listed is not None and listed in await twilio.lists_on(trunk.sid)
    )
    pointed = f"Twilio: a trunk sending calls to {origination_uri(domain)}"
    terminated = trunk is not None and trunk.domain_name == host
    steps = [
        f"{pointed}: {'stands' if trunk else 'to do'}",
        f"Twilio: dialled at {host}: {'stands' if terminated else 'to do'}",
        f"Twilio: credential list {name}: {'stands' if listed else 'to do'}",
        f"Twilio: the org's credential {username} on it: {'stands' if holds else 'to do'}",
        f"Twilio: the trunk asks for it: {'stands' if on_trunk else 'to do'}",
    ]
    at_twilio = TwilioSurvey(
        twilio=twilio,
        trunk=trunk,
        host=host,
        list_name=name,
        listed=listed,
        username=username,
        holds=holds,
        on_trunk=on_trunk,
    )
    return OutboundSurvey(steps=steps, address=host, twilio=at_twilio)


async def _twilio_provisioned(
    connections: Connections, carrier: Carrier, survey: TwilioSurvey, world: Env
) -> str:
    domain = domain_of(connections, world)
    twilio = survey.twilio
    trunk = survey.trunk or await twilio.trunk_made(domain, origination_uri(domain))
    if trunk.domain_name != survey.host:
        await twilio.terminated(trunk.sid, survey.host)
    listed = survey.listed or await twilio.credential_list_made(survey.list_name)
    outbound = carrier.outbound
    if not survey.holds:
        outbound = Termination(
            host=survey.host, username=survey.username, password=secrets.token_urlsafe(24)
        )
        await twilio.credential_added(listed, outbound.username, outbound.password)
    if not survey.on_trunk:
        await twilio.list_attached(trunk.sid, listed)
    await seal_carrier(connections.pool, connections.vault, replace(carrier, outbound=outbound))
    return trunk.sid


async def _dials_through(connections: Connections, scope: Scope, shown: str) -> Carrier:
    rows = await _calling_from(connections.pool, scope)
    account = next((row["account"] for row in rows if row["number"] == shown), None)
    if account is None:
        raise Conflict(DIALS_THROUGH_NOTHING.format(shown=shown))
    carrier = await carrier_named(connections.pool, connections.vault, scope.org, str(account))
    missing = _missing_to_dial(carrier)
    if missing:
        raise Conflict(missing[0])
    return carrier


def _dialled_through(carrier: Carrier, shown: str) -> LegTrunk:
    match carrier.account:
        case TwilioAccount():
            termination = carrier.outbound
            if termination is None:
                raise Conflict(NOT_PROVISIONED.format(account=carrier.id))
            return LegTrunk(
                hostname=termination.host,
                transport="auto",
                username=termination.username,
                password=termination.password,
                shown=shown,
            )
        case SipPeer():
            peer = carrier.account
            return LegTrunk(
                hostname=str(peer.outbound_host),
                transport=peer.outbound_transport,
                username=peer.outbound_username or peer.username,
                password=peer.outbound_password or peer.password,
                shown=shown,
            )
        case WhatsappAccount():
            raise Conflict(_missing_to_dial(carrier)[0])


async def _calling_from(pool: Pool, scope: Scope) -> list[DictRow]:
    async with pool.connection() as connection:
        return await (
            await connection.execute(CALLING_FROM, {"org": scope.org, "env": scope.env})
        ).fetchall()

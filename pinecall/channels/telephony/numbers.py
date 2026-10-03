"""A number imported, bought, released or moved: at the carrier, on the SFU, in the routes table."""

import logging
from dataclasses import dataclass, field, replace

import httpx
from livekit.protocol.sip import (
    SIPDispatchRuleInfo,
    SIPInboundTrunkInfo,
)

from pinecall.channels import routes, whatsapp
from pinecall.channels.routes import RouteRecord, RouteWrite
from pinecall.channels.telephony import carrier_catalog, sip
from pinecall.channels.telephony._twilio import (
    Twilio,
    TwilioNumber,
    TwilioTrunk,
    origination_uri,
    twilio_of,
)
from pinecall.channels.telephony.carrier import (
    Fence,
    control_of,
    declared_networks,
    fence_of,
    meta_of,
)
from pinecall.channels.telephony.carrier_catalog import known_carrier
from pinecall.channels.telephony.sip import WorldRule, domain_of, rule_name
from pinecall.domain.call import Route
from pinecall.domain.errors import (
    Conflict,
    DeclarationRefused,
    NotFound,
    UpstreamFailed,
)
from pinecall.domain.names import Channel, Env, RouteOrigin, parse_e164
from pinecall.domain.scope import Scope
from pinecall.fleet import worlds
from pinecall.process.connections import Connections
from pinecall.tenancy import admission, carrier_networks
from pinecall.tenancy.carriers import (
    NO_CARRIER,
    WhatsappAccount,
    box_twilio,
    carrier_named,
    carriers_of,
)

logger = logging.getLogger(__name__)


NOT_ON_ACCOUNT = "{number} is not a number of Twilio account {account}"


ON_ANOTHER_TRUNK = (
    "{number} is on Twilio trunk {trunk} of {account}, pointed at {where}: moving it here takes it "
    "off there, so say move"
)


HELD_ELSEWHERE = (
    "{number} is on another org's trunk on this box ({trunk}): livekit-sip refuses an INVITE two "
    "trunks list, so nothing was written"
)


NO_DOMAIN = "this box has no PINECALL_DOMAIN: a carrier has nowhere to send a call"


NOT_A_PHONE = "a number is imported to answer phone or whatsapp, not {channel}"


KEY_WITHOUT_PEER = "a {kind} account takes no networks: they are the fence of a number you hook"


NO_ROUTE = "the org answers no {number} in the {env}"


NONE_FOR_SALE = "Twilio has no local voice number for sale in {country}{area}"


VIA_ALONE = (
    "a number comes via a carrier of the box's catalog when the org hooks it, on the phone, with "
    "no networks of its own: that carrier's are its fence"
)


NOT_ADMITTED = "this box does not admit {name}: its operator turns it on under Carriers"


NOT_A_COUNTRY = "{number} starts with no country calling code E.164 assigns"


@dataclass(frozen=True)
class NumberPurchase:
    """A number wanted from the box's own account."""

    scope: Scope
    agent: str
    country: str
    area_code: str | None = None
    channel: Channel = "phone"


@dataclass(frozen=True)
class AtTwilio:
    """The number at its Twilio account, and the trunk that points it here."""

    twilio: Twilio
    number: TwilioNumber | None
    trunk: TwilioTrunk | None
    # Bought in this very request: a dry run names it and buys nothing.
    for_sale: str | None = None


@dataclass
class Survey:
    """Everything a hook needs, read before anything is written."""

    route: Route
    origin: RouteOrigin
    fence: Fence | None
    account: str | None
    networks: tuple[str, ...]
    fleet: str
    domain: str
    at_twilio: AtTwilio | None = None
    via: str | None = None
    # The networks named for the number that the operator has not approved: no trunk until he does.
    waiting: tuple[str, ...] = ()
    # The org's own other trunks that list the number and must let it go.
    leaving: list[SIPInboundTrunkInfo] = field(default_factory=list[SIPInboundTrunkInfo])
    trunk: SIPInboundTrunkInfo | None = None
    rule: SIPDispatchRuleInfo | None = None
    other_rule: SIPDispatchRuleInfo | None = None
    routed: Route | None = None


@dataclass(frozen=True)
class Plan:
    """What a hook does, one sentence a step, and the route it ends in."""

    route: Route
    steps: list[str]
    dry_run: bool


@dataclass(frozen=True)
class OwnedNumber:
    """A number one of the org's accounts owns, and whether this world already imported it."""

    number: str
    name: str
    imported: bool
    account: str


@dataclass(frozen=True)
class NumberImport:
    """A number wanted: where it answers, and how it reaches the box."""

    scope: Scope
    agent: str
    number: str
    channel: Channel = "phone"
    # The account it lives in; unsaid, the org's only one.
    account: str | None = None
    # The org points the number here itself: no account is touched.
    hooked: bool = False
    # A self-hooked number's own networks, which the operator approves; unsaid, its carrier's.
    networks: tuple[str, ...] = ()
    # The catalog carrier a self-hooked number comes through; unsaid, Twilio.
    via: str | None = None
    # Take the number off another trunk of its account first.
    move: bool = False


async def plan_buy(connections: Connections, wanted: NumberPurchase) -> Plan:
    """What buying would do: the number Twilio would sell, and nothing bought."""
    survey = await _survey_purchase(connections, wanted)
    return Plan(route=survey.route, steps=_steps(survey, done=False), dry_run=True)


async def buy_number(connections: Connections, wanted: NumberPurchase) -> Plan:
    """Buy a number on the box's account and hook it as an import, counted as the box's."""
    survey = await _survey_purchase(connections, wanted)
    steps = _steps(survey, done=True)
    at = survey.at_twilio
    if at is not None and at.for_sale is not None:
        survey.at_twilio = replace(at, number=await at.twilio.bought(at.for_sale), for_sale=None)
    await _write_hook(connections, survey)
    return Plan(route=survey.route, steps=steps, dry_run=False)


async def release(connections: Connections, org: str, number: str, env: Env) -> None:
    """Let the number go: the route, the SFU's admission, its world's rule; the account keeps it."""
    route = await routes.of_number(connections.pool, org, number)
    if route is None or route.env != env:
        raise NotFound(NO_ROUTE.format(number=number, env=env))
    await routes.remove(connections.pool, org, number)
    for trunk in await sip.trunks_admitting(connections.server, number):
        if sip.belongs_to(trunk.name, org):
            kept = [listed for listed in trunk.numbers if listed != number]
            await sip.renumber_trunk(connections.server, org, trunk, kept)
    rule = await sip.rule_named(connections.server, rule_name(org, env))
    if rule is not None:
        await sip.renumber_rule(
            connections.server, rule, [listed for listed in rule.numbers if listed != number]
        )


async def move(connections: Connections, org: str, number: str, env: Env) -> RouteRecord:
    """Move the number into the other world: its row and the two rules, the trunk untouched."""
    record = await routes.record_of(connections.pool, org, number)
    if record is None:
        raise NotFound(NO_ROUTE.format(number=number, env="either world"))
    route = record.route
    if route.env == env:
        return record
    moved = replace(record, route=replace(route, env=env))
    # A WhatsApp number is on no trunk: its world is its row alone.
    if route.channel != "phone":
        await routes.moved(connections.pool, org, number, env)
        return moved
    fleets = await worlds.fleets(connections.pool)
    trunks = [
        trunk.sip_trunk_id
        for trunk in await sip.trunks_admitting(connections.server, number)
        if sip.belongs_to(trunk.name, org)
    ]
    left = await sip.rule_named(connections.server, rule_name(org, route.env))
    if left is not None:
        await sip.renumber_rule(
            connections.server, left, [listed for listed in left.numbers if listed != number]
        )
    await sip.rule_in(
        connections.server, WorldRule(org, env, worlds.fleet_of(fleets, env)), trunks, number
    )
    await routes.moved(connections.pool, org, number, env)
    return moved


# A SIP peer owns what it owns and nobody here can list it: its import takes the number typed.
async def owned_numbers(
    connections: Connections, scope: Scope, account: str | None = None
) -> tuple[str, list[OwnedNumber]]:
    """The kind of the org's accounts, and every number its Twilio accounts own."""
    if account is None:
        carriers = await carriers_of(connections.pool, connections.vault, scope.org)
    else:
        carriers = [await carrier_named(connections.pool, connections.vault, scope.org, account)]
    if not carriers:
        raise NotFound(NO_CARRIER)
    answering = await routes.of_org(connections.pool, scope.org, scope.env)
    imported = {route.number for route in answering if route.number is not None}
    controlled = [
        (carrier, control)
        for carrier in carriers
        if (control := control_of(connections.http, carrier)) is not None
    ]
    owned = [
        OwnedNumber(
            number=number.phone_number,
            name=number.friendly_name or number.phone_number,
            imported=number.phone_number in imported,
            account=carrier.id,
        )
        for carrier, control in controlled
        for number in await control.numbers()
    ]
    for carrier in carriers:
        meta = meta_of(carrier)
        if meta is not None:
            owned += await _at_meta(connections.http, meta, imported)
    lister = controlled[0][0] if controlled else carriers[0]
    return lister.account.kind, owned


async def import_number(connections: Connections, wanted: NumberImport) -> Plan:
    """Hook the number at its account, admit it on the SFU in its world, route it."""
    survey = await _survey_import(connections, wanted)
    steps = _steps(survey, done=True)
    await _write_hook(connections, survey)
    return Plan(route=survey.route, steps=steps, dry_run=False)


# The operator's row is admitted like a hooked number, at once, and says who wrote it.
async def type_route(connections: Connections, wanted: NumberImport) -> Plan:
    """Route a number the box's operator names: on the SFU and in the table, no account touched."""
    survey = await _survey_import(connections, replace(wanted, hooked=True, account=None))
    survey.origin = "typed"
    steps = _steps(survey, done=True)
    await _write_hook(connections, survey)
    return Plan(route=survey.route, steps=steps, dry_run=False)


async def plan_import(connections: Connections, wanted: NumberImport) -> Plan:
    """What importing the number would do, writing nothing."""
    survey = await _survey_import(connections, wanted)
    return Plan(route=survey.route, steps=_steps(survey, done=False), dry_run=True)


async def _survey_import(connections: Connections, wanted: NumberImport) -> Survey:
    number = parse_e164(wanted.number)
    if wanted.channel not in {"phone", "whatsapp"}:
        raise DeclarationRefused(NOT_A_PHONE.format(channel=wanted.channel))
    org, env = wanted.scope.org, wanted.scope.env
    domain = domain_of(connections, env)
    route = Route(org=org, agent=wanted.agent, channel=wanted.channel, number=number, env=env)
    fleets = await worlds.fleets(connections.pool)
    await _check_via(connections, wanted)
    carrier = (
        None
        if wanted.hooked
        else await carrier_named(connections.pool, connections.vault, org, wanted.account)
    )
    if carrier is not None and wanted.networks:
        raise DeclarationRefused(KEY_WITHOUT_PEER.format(kind=carrier.account.kind))
    networks = tuple(carrier_networks.checked(network) for network in wanted.networks)
    approved = await carrier_networks.approved(connections.pool, org)
    named = (networks or None) if carrier is None else declared_networks(carrier)
    declared = None if named is None else tuple(carrier_networks.written(n) for n in named)
    source = number if carrier is None else carrier.id
    own = None if declared is None else tuple(n for n in declared if n in approved.get(source, ()))
    survey = Survey(
        route=route,
        origin="hooked" if carrier is None else "imported",
        fence=fence_of(org, number, carrier, wanted.via, own)
        if wanted.channel == "phone"
        else None,
        account=None if carrier is None else carrier.id,
        networks=networks,
        fleet=worlds.fleet_of(fleets, env),
        domain=domain,
        via=wanted.via,
        waiting=() if declared is None else tuple(n for n in declared if n not in (own or ())),
    )
    control = None if carrier is None else control_of(connections.http, carrier)
    if control is not None and wanted.channel == "phone":
        survey.at_twilio = await _at_twilio(connections, control, number, env, move=wanted.move)
    return await _survey_sfu(connections, survey)


# A catalog carrier is the operator's to admit; its networks are the number's whole fence.
async def _check_via(connections: Connections, wanted: NumberImport) -> None:
    if wanted.via is None:
        return
    if not wanted.hooked or wanted.networks or wanted.channel != "phone":
        raise DeclarationRefused(VIA_ALONE)
    carrier = known_carrier(wanted.via)
    if wanted.via not in await carrier_catalog.admitted(connections.pool):
        raise Conflict(NOT_ADMITTED.format(name=carrier.name))


async def _survey_purchase(connections: Connections, wanted: NumberPurchase) -> Survey:
    org, env = wanted.scope.org, wanted.scope.env
    domain = domain_of(connections, env)
    boxs = await box_twilio(connections.pool, connections.vault)
    await admission.admit_number(
        connections.pool, org, env, bought=await routes.managed_in(connections.pool, org, env)
    )
    twilio = twilio_of(connections.http, boxs)
    for_sale = await twilio.for_sale(wanted.country, wanted.area_code)
    if for_sale is None:
        area = f", area code {wanted.area_code}" if wanted.area_code else ""
        raise NotFound(NONE_FOR_SALE.format(country=wanted.country.upper(), area=area))
    route = Route(
        org=org, agent=wanted.agent, channel=wanted.channel, number=for_sale, env=env, managed=True
    )
    fleets = await worlds.fleets(connections.pool)
    survey = Survey(
        route=route,
        origin="bought",
        fence=fence_of(org, for_sale, None, None, None),
        account=None,
        networks=(),
        fleet=worlds.fleet_of(fleets, env),
        domain=domain,
        at_twilio=AtTwilio(
            twilio=twilio,
            number=None,
            trunk=await twilio.trunk_pointing_at(origination_uri(domain)),
            for_sale=for_sale,
        ),
    )
    return await _survey_sfu(connections, survey)


async def _at_twilio(
    connections: Connections, twilio: Twilio, number: str, world: Env, *, move: bool
) -> AtTwilio:
    owned = await twilio.number(number)
    if owned is None:
        raise NotFound(NOT_ON_ACCOUNT.format(number=number, account=twilio.sid))
    trunk = await twilio.trunk_pointing_at(origination_uri(domain_of(connections, world)))
    elsewhere = owned.trunk_sid is not None and (trunk is None or owned.trunk_sid != trunk.sid)
    if elsewhere and owned.trunk_sid is not None and not move:
        where = await twilio.originations(owned.trunk_sid)
        other = await twilio.trunk(owned.trunk_sid)
        raise Conflict(
            ON_ANOTHER_TRUNK.format(
                number=number,
                trunk=other.friendly_name or other.sid,
                account=twilio.sid,
                where=", ".join(where) or "nowhere",
            )
        )
    return AtTwilio(twilio=twilio, number=owned, trunk=trunk)


async def _survey_sfu(connections: Connections, survey: Survey) -> Survey:
    route, fence = survey.route, survey.fence
    number = str(route.number)
    survey.routed = await routes.of_number(connections.pool, route.org, number)
    if route.channel != "phone":
        return survey
    listing = await sip.trunks_admitting(connections.server, number)
    stranger = next((trunk for trunk in listing if not sip.belongs_to(trunk.name, route.org)), None)
    if stranger is not None:
        raise Conflict(HELD_ELSEWHERE.format(number=number, trunk=stranger.name))
    survey.leaving = [trunk for trunk in listing if fence is None or trunk.name != fence.trunk]
    if fence is None:
        return survey
    survey.trunk = await sip.trunk_named(connections.server, fence.trunk)
    survey.rule = await sip.rule_named(connections.server, rule_name(route.org, route.env))
    other = "sandbox" if route.env == "production" else "production"
    survey.other_rule = await sip.rule_named(connections.server, rule_name(route.org, other))
    return survey


async def _write_hook(connections: Connections, survey: Survey) -> None:
    route, number = survey.route, str(survey.route.number)
    at = survey.at_twilio
    if at is not None and at.number is not None:
        trunk = at.trunk or await at.twilio.trunk_made(
            survey.domain, origination_uri(survey.domain)
        )
        survey.at_twilio = replace(at, trunk=trunk)
        if at.number.trunk_sid not in {None, trunk.sid}:
            await at.twilio.detach(str(at.number.trunk_sid), at.number.sid)
        if at.number.trunk_sid != trunk.sid:
            await at.twilio.attach(trunk.sid, at.number.sid)
    if survey.origin == "hooked" and survey.networks:
        await carrier_networks.ask(connections.pool, route.org, number, survey.networks)
    for leaving in survey.leaving:
        kept = [listed for listed in leaving.numbers if listed != number]
        await sip.renumber_trunk(connections.server, route.org, leaving, kept)
    if survey.fence is not None:
        # Off the other world's rule first: two rules of one trunk never list one number.
        if survey.other_rule is not None and number in survey.other_rule.numbers:
            kept = [listed for listed in survey.other_rule.numbers if listed != number]
            await sip.renumber_rule(connections.server, survey.other_rule, kept)
        trunk_id = await sip.admit(connections.server, survey.fence, survey.trunk, number)
        world = WorldRule(route.org, route.env, survey.fleet)
        await sip.rule_in(connections.server, world, [trunk_id], number)
    written = RouteWrite(
        origin=survey.origin, account=survey.account, networks=survey.networks, via=survey.via
    )
    await routes.put(connections.pool, route, written)


def _steps(survey: Survey, *, done: bool) -> list[str]:
    route, number = survey.route, str(survey.route.number)
    made = "done" if done else "to do"
    steps: list[str] = []
    at = survey.at_twilio
    if at is not None:
        if at.for_sale is not None:
            steps.append(f"Twilio: buy {at.for_sale} on the box's account: {made}")
        pointed = f"Twilio: a trunk sending calls to {origination_uri(survey.domain)}"
        steps.append(f"{pointed}: {'stands' if at.trunk else made}")
        on_it = (
            at.number is not None and at.trunk is not None and at.number.trunk_sid == at.trunk.sid
        )
        steps.append(f"Twilio: {number} attached to it: {'stands' if on_it else made}")
    fence = survey.fence
    steps += [f"LiveKit: {number} off trunk {trunk.name}: {made}" for trunk in survey.leaving]
    if fence is None and survey.waiting and route.channel == "phone":
        waiting = ", ".join(survey.waiting)
        steps.append(f"LiveKit: {number} waits for the box's operator to approve {waiting}: {made}")
    if fence is not None:
        admits = survey.trunk is not None and number in survey.trunk.numbers
        steps.append(
            f"LiveKit: trunk {fence.trunk} admits {number}: {'stands' if admits else made}"
        )
        rule = rule_name(route.org, route.env)
        ruled = survey.rule is not None and number in survey.rule.numbers
        steps.append(
            f"LiveKit: rule {rule} sends it to fleet {survey.fleet}: {'stands' if ruled else made}"
        )
    routed = survey.routed == route
    steps.append(
        f"route: {number} to {route.agent} in the {route.env}: {'stands' if routed else made}"
    )
    return steps


# A token Meta refuses (they expire) leaves the account listed with no number, said in the log.
async def _at_meta(
    http: httpx.AsyncClient, account: WhatsappAccount, imported: set[str]
) -> list[OwnedNumber]:
    try:
        number, name = await whatsapp.display_number(
            http, account.access_token, account.phone_number_id
        )
    except UpstreamFailed:
        logger.warning(
            "Meta did not say which number %s is", account.phone_number_id, exc_info=True
        )
        return []
    return [
        OwnedNumber(
            number=number,
            name=name or number,
            imported=number in imported,
            account=account.phone_number_id,
        )
    ]

"""The number doors: the org's carrier accounts, its numbers, dialling out, and a worker's leg."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from pinecall.channels import routes
from pinecall.channels.telephony import carrier_catalog, dialing, number_path, numbers, sip
from pinecall.channels.telephony.carrier import declared_networks, verify_account
from pinecall.channels.telephony.dialing import Placement
from pinecall.channels.telephony.numbers import NumberImport, NumberPurchase
from pinecall.domain.call import today_in
from pinecall.domain.errors import Conflict, NotAvailable, NotFound
from pinecall.domain.names import parse_e164
from pinecall.domain.scope import Scope
from pinecall.gateway._deps import (
    Acting,
    CallsKey,
    GatewayDep,
    NumbersKey,
    ScopeDep,
    TalkKey,
    WorkerKey,
    asked_by,
)
from pinecall.gateway._gateway import Gateway
from pinecall.tenancy import carrier_networks, carriers, consents, keys, tokens
from pinecall.tenancy.carrier_networks import NetworkAsk
from pinecall.tenancy.carriers import Carrier
from pinecall.tenancy.consents import Given
from pinecall.tenancy.dial_policy import Dial
from pinecall.wire.rest.numbers import (
    AvailableNumbers,
    BuyNumberRequest,
    CarrierCatalog,
    CarrierList,
    CarrierRow,
    CatalogCarrier,
    ConsentHistory,
    DialGuards,
    DialRequest,
    DialResponse,
    DoNotCall,
    DoNotCallImport,
    DoNotCallImported,
    ImportNumberRequest,
    ImportNumberResponse,
    LegTrunkResponse,
    MoveNumberRequest,
    NetworkRow,
    NumberPath,
    NumberRow,
    OutboundStatus,
    OwnedNumberRow,
    PathStep,
    ProvisionOutboundResponse,
    RecordConsent,
)

NO_SUCH_NUMBER = "the org answers at no {number} in the {env}"


router = APIRouter()


# Who the trail names for an opt-out written at a door, with no form behind it.
ASKED_BY_HAND = "asked at the console or the API"


NOBODY_HOLDING = (
    "nobody holds agent {agent} in this corner: a phone would ring with no app to serve it"
)


# A person or a server acts in the org's own scope of its world: numbers are the org's.
AccountAsked = Annotated[str | None, Query()]


DryRun = Annotated[bool, Query()]


class LegTrunkQuery(BaseModel):
    """A worker asking how to dial a leg of a call: to whom, from which of the org's numbers."""

    model_config = ConfigDict(populate_by_name=True)

    to: str
    call: str
    shown: str | None = Field(None, alias="from")


@router.get("/v1/carrier")
async def get_carrier(
    key: NumbersKey, gateway: GatewayDep, account: AccountAsked = None
) -> CarrierRow:
    """The org's account, named, never its secret; the only one unless one is asked for."""
    carrier = await carriers.carrier_named(
        gateway.connections.pool, gateway.connections.vault, key.org, account
    )
    asks = await carrier_networks.of_org(gateway.connections.pool, key.org)
    return _carrier_row(carrier, asks)


@router.get("/v1/carriers")
async def list_carriers(key: NumbersKey, gateway: GatewayDep) -> CarrierList:
    """Every account of the org, oldest first."""
    carriers_listed = await carriers.carriers_of(
        gateway.connections.pool, gateway.connections.vault, key.org
    )
    asks = await carrier_networks.of_org(gateway.connections.pool, key.org)
    return CarrierList(carriers=[_carrier_row(carrier, asks) for carrier in carriers_listed])


# What Add a number offers beside the org's own accounts: Twilio drives its own numbers; a
# guided carrier is SIP terms, a number pasted into its portal.
@router.get("/v1/carriers/catalog")
async def list_carrier_catalog(_key: NumbersKey, gateway: GatewayDep) -> CarrierCatalog:
    """The carriers this box admits, each automatic or guided, and whether the box sells numbers."""
    connections = gateway.connections
    admitted = await carrier_catalog.admitted(connections.pool)
    try:
        await carriers.box_twilio(connections.pool, connections.vault)
        sells = True
    except NotAvailable:
        sells = False
    return CarrierCatalog(
        carriers=[
            CatalogCarrier(
                kind=carrier.kind,
                name=carrier.name,
                how="automatic" if carrier.control else "guided",
                networks=list(carrier.networks),
            )
            for carrier in carrier_catalog.known().values()
            if carrier.kind in admitted
        ],
        sells=sells,
    )


# Bringing an account the org already holds replaces its secret; a Twilio pair is tried first.
@router.put("/v1/carrier")
async def put_carrier(body: carriers.Account, key: NumbersKey, gateway: GatewayDep) -> CarrierRow:
    """Keep an account of the org: a Twilio account, a SIP peer, a WhatsApp number at Meta."""
    connections = gateway.connections
    declared = declared_networks(Carrier(org=key.org, account=body))
    for network in declared or ():
        carrier_networks.checked(network)
    await verify_account(connections.http, body)
    carrier = await carriers.put_carrier(connections.pool, connections.vault, key.org, body)
    if declared is not None:
        await carrier_networks.ask(connections.pool, key.org, carrier.id, declared)
        await sip.readmit(connections, key.org)
    return _carrier_row(carrier, await carrier_networks.of_org(connections.pool, key.org))


@router.delete("/v1/carrier", status_code=204)
async def drop_carrier(key: NumbersKey, gateway: GatewayDep, account: AccountAsked = None) -> None:
    """Forget an account; its numbers stay routed until each is let go."""
    await carriers.drop_carrier(
        gateway.connections.pool, gateway.connections.vault, key.org, account
    )


@router.get("/v1/numbers")
async def list_numbers(key: NumbersKey, gateway: GatewayDep) -> list[NumberRow]:
    """The org's numbers in the key's world, and the agent each reaches."""
    records = await routes.records_of(gateway.connections.pool, key.org, key.env)
    running_agents = _running(gateway, Scope(org=key.org, env=key.env))
    rings = await number_path.rings_of(
        gateway.connections, records, lambda record: record.route.agent in running_agents
    )
    return [
        NumberRow(
            route=record.route,
            origin=record.origin,
            rings=ringing,
            last_call_at=record.last_call_at,
            via=record.via,
        )
        for record, ringing in zip(records, rings, strict=True)
    ]


@router.get("/v1/numbers/{number}/path")
async def number_path_of(number: str, key: NumbersKey, gateway: GatewayDep) -> NumberPath:
    """What a call to the number goes through now: its carrier, the fence, the world, the agent."""
    record = await routes.record_of(gateway.connections.pool, key.org, parse_e164(number))
    if record is None or record.route.env != key.env:
        raise NotFound(NO_SUCH_NUMBER.format(number=number, env=key.env))
    running_agents = _running(gateway, Scope(org=key.org, env=key.env))
    found = await number_path.path_of(
        gateway.connections, record, running=record.route.agent in running_agents
    )
    return NumberPath(
        number=str(record.route.number),
        steps=[
            PathStep(step=step.step, state=step.state, says=step.says, fix=step.fix)
            for step in found.steps
        ],
        rings=found.rings,
        last_call_at=record.last_call_at,
    )


@router.get("/v1/numbers/available")
async def list_available_numbers(
    key: NumbersKey, gateway: GatewayDep, account: AccountAsked = None
) -> AvailableNumbers:
    """What the org's Twilio accounts own, and which of it this world imported."""
    kind, owned = await numbers.owned_numbers(gateway.connections, _org_scope(key), account)
    return AvailableNumbers(
        kind=kind,
        numbers=[
            OwnedNumberRow(
                number=owned_number.number,
                name=owned_number.name,
                imported=owned_number.imported,
                account=owned_number.account,
            )
            for owned_number in owned
        ],
    )


@router.post("/v1/numbers")
async def import_number(
    body: ImportNumberRequest, key: NumbersKey, gateway: GatewayDep, *, dry_run: DryRun = False
) -> ImportNumberResponse:
    """Hook a number at its account, admit it on the SFU in the key's world, route it."""
    keys.check_agent(key.bearer, body.agent)
    wanted = NumberImport(
        scope=_org_scope(key),
        agent=body.agent,
        number=body.number,
        channel=body.channel,
        account=body.account,
        hooked=body.hooked,
        networks=tuple(body.networks),
        via=body.via,
        move=body.move,
    )
    plan = (
        await numbers.plan_import(gateway.connections, wanted)
        if dry_run
        else await numbers.import_number(gateway.connections, wanted)
    )
    return ImportNumberResponse(route=plan.route, steps=plan.steps, dry_run=plan.dry_run)


@router.post("/v1/numbers/buy")
async def buy_number(
    body: BuyNumberRequest, key: NumbersKey, gateway: GatewayDep, *, dry_run: DryRun = False
) -> ImportNumberResponse:
    """Buy a number on the box's account and hook it, counted against the world's stock."""
    keys.check_agent(key.bearer, body.agent)
    wanted = NumberPurchase(
        scope=_org_scope(key),
        agent=body.agent,
        country=body.country,
        area_code=body.area_code,
        channel=body.channel,
    )
    plan = (
        await numbers.plan_buy(gateway.connections, wanted)
        if dry_run
        else await numbers.buy_number(gateway.connections, wanted)
    )
    return ImportNumberResponse(route=plan.route, steps=plan.steps, dry_run=plan.dry_run)


@router.delete("/v1/numbers/{number}", status_code=204)
async def release_number(number: str, key: NumbersKey, gateway: GatewayDep) -> None:
    """Let the number go: its route and its admission; the account keeps it."""
    await numbers.release(gateway.connections, key.org, parse_e164(number), key.env)


@router.put("/v1/numbers/{number}/env")
async def move_number(
    number: str, body: MoveNumberRequest, key: NumbersKey, gateway: GatewayDep
) -> NumberRow:
    """Move the number into the other world: its row and the two rules."""
    moved = await numbers.move(gateway.connections, key.org, parse_e164(number), body.env)
    return NumberRow(route=moved.route, origin=moved.origin)


@router.get("/v1/carrier/outbound")
async def get_outbound_status(
    key: NumbersKey, gateway: GatewayDep, account: AccountAsked = None
) -> OutboundStatus:
    """Whether the org can place a call, one sentence per thing missing, and its guards."""
    readiness = await dialing.outbound_readiness(gateway.connections, _org_scope(key), account)
    return OutboundStatus(
        ready=readiness.ready,
        kind=readiness.kind,
        from_numbers=readiness.from_numbers,
        steps_missing=readiness.steps_missing,
        guards=DialGuards.model_validate(readiness.guards.model_dump()),
    )


@router.post("/v1/carrier/outbound", response_model_exclude_unset=True)
async def provision_outbound(
    key: NumbersKey, gateway: GatewayDep, *, account: AccountAsked = None, dry_run: DryRun = False
) -> ProvisionOutboundResponse:
    """Make an account dialable: Twilio's termination and a credential; a peer needs nothing."""
    done = (
        await dialing.plan_outbound(gateway.connections, key.org, key.env, account)
        if dry_run
        else await dialing.provision_outbound(gateway.connections, key.org, key.env, account)
    )
    if done.dry_run:
        return ProvisionOutboundResponse(steps=done.steps, dry_run=True, ready=False)
    return ProvisionOutboundResponse(
        steps=done.steps, dry_run=False, ready=done.ready, trunk=done.trunk, address=done.address
    )


@router.post("/v1/agents/{slug}/dial", status_code=202)
async def dial_out(
    slug: str, body: DialRequest, key: TalkKey, where: ScopeDep, gateway: GatewayDep
) -> DialResponse:
    """Place a call as the agent, after its guards: the call it became, before anything rings."""
    if gateway.sockets.serving(where, slug, None) is None:
        raise Conflict(NOBODY_HOLDING.format(agent=slug))
    who = asked_by(key)
    placement = Placement(
        scope=where,
        agent=slug,
        to=body.to,
        shown=body.from_,
        asked_by=who,
        today=today_in(gateway.connections.settings.timezone),
        at=datetime.now(UTC),
        own_phone=gateway.sockets.phone_of(where.env, body.to) == who,
        consent=body.consent,
    )
    placed = await dialing.place_call(
        gateway.connections,
        gateway.logs,
        placement,
        running=gateway.live.running(where.org, where.env),
    )
    return DialResponse.model_validate(
        {
            "call": placed.call,
            "agent": slug,
            "to": placed.to,
            "from": placed.shown,
            "env": where.env,
            "log_token": tokens.log_token(gateway.signer, placed.call, body.log),
        }
    )


# The worker's, for a leg into a live call and for the leg a dial placed.
@router.get("/v1/agents/{slug}/outbound-trunk")
async def get_leg_trunk(
    slug: str,
    key: WorkerKey,
    where: ScopeDep,
    gateway: GatewayDep,
    query: Annotated[LegTrunkQuery, Query()],
) -> LegTrunkResponse:
    """The leg's trunk inline, after the shape and the pace; a dial's own first leg passes."""
    dial = Dial(where, slug, query.to, query.shown, asked_by(key), query.call, datetime.now(UTC))
    return LegTrunkResponse(trunk=await dialing.leg_trunk(gateway.connections, dial))


# ── consent and the do-not-call list ──


@router.post("/v1/org/consents")
async def record_consent(
    body: RecordConsent, key: TalkKey, where: ScopeDep, gateway: GatewayDep
) -> ConsentHistory:
    """Write one fact about a number, a consent or an opt-out; what stands for it after."""
    given = Given(
        kind=body.kind,
        source=body.source,
        given_by=asked_by(key),
        text=body.text,
        evidence=body.evidence,
    )
    pool = gateway.connections.pool
    await consents.give(pool, where, body.number, given)
    return await consents.history(pool, where, body.number)


@router.get("/v1/org/consents/{number}")
async def consent_of(
    number: str, _key: CallsKey, where: ScopeDep, gateway: GatewayDep
) -> ConsentHistory:
    """What stands for the number in the key's world, and every fact about it, newest first."""
    return await consents.history(gateway.connections.pool, where, number)


# An opt-out is a fact, not a deletion: the consent before it stays in the history.
@router.delete("/v1/org/consents/{number}")
async def opt_out(
    number: str, key: TalkKey, where: ScopeDep, gateway: GatewayDep
) -> ConsentHistory:
    """Put the number on the do-not-call list: no call of the org's reaches it from now on."""
    given = Given(kind="opt_out", source=ASKED_BY_HAND, given_by=asked_by(key))
    pool = gateway.connections.pool
    await consents.give(pool, where, number, given)
    return await consents.history(pool, where, number)


@router.get("/v1/org/dnc")
async def do_not_call(
    _key: CallsKey, where: ScopeDep, gateway: GatewayDep, after: str | None = None
) -> DoNotCall:
    """The world's do-not-call list, newest first, a page after the cursor."""
    return await consents.do_not_call(gateway.connections.pool, where, after=after)


@router.post("/v1/org/dnc")
async def import_do_not_call(
    body: DoNotCallImport, key: TalkKey, where: ScopeDep, gateway: GatewayDep
) -> DoNotCallImported:
    """Numbers the org's own list or its Registry scrub says not to call, onto the list at once."""
    given = Given(kind="opt_out", source=body.source, given_by=asked_by(key))
    added, refused = await consents.opt_out_many(
        gateway.connections.pool, where, body.numbers, given
    )
    return DoNotCallImported(added=added, refused=refused)


def _running(gateway: Gateway, scope: Scope) -> set[str]:
    """The agents a process holds in the org's world now, in any of its scopes."""
    return {registration.slug for registration in gateway.sockets.holding(scope, every_corner=True)}


def _carrier_row(carrier: Carrier, asks: list[NetworkAsk]) -> CarrierRow:
    """An account as the doors send it, a peer's networks with the operator's answer to each."""
    return CarrierRow(
        kind=carrier.account.kind,
        account=carrier.id,
        label=carrier.account.label,
        networks=[
            NetworkRow(network=ask.network, state=ask.state)
            for ask in asks
            if ask.source == carrier.id
        ],
    )


def _org_scope(key: Acting) -> Scope:
    return Scope(key.org, key.env)

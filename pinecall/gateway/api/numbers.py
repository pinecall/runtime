"""The number doors: the org's carrier accounts, its numbers, dialling out, and a worker's leg."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict, Field

from pinecall.channels import routes
from pinecall.channels.telephony import dialing, numbers
from pinecall.channels.telephony.carrier import verify_account
from pinecall.channels.telephony.dialing import Placement
from pinecall.channels.telephony.numbers import NumberImport, NumberPurchase
from pinecall.domain.call import today_in
from pinecall.domain.errors import Conflict
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
from pinecall.tenancy import carriers, consents, keys, tokens
from pinecall.tenancy.consents import Given
from pinecall.tenancy.dial_policy import Dial
from pinecall.wire.rest.numbers import (
    AvailableNumbers,
    BuyNumberRequest,
    CarrierList,
    CarrierRow,
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
    NumberRow,
    OutboundStatus,
    OwnedNumberRow,
    ProvisionOutboundResponse,
    RecordConsent,
)

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
    return CarrierRow(kind=carrier.account.kind, account=carrier.id, label=carrier.account.label)


@router.get("/v1/carriers")
async def list_carriers(key: NumbersKey, gateway: GatewayDep) -> CarrierList:
    """Every account of the org, oldest first."""
    carriers_listed = await carriers.carriers_of(
        gateway.connections.pool, gateway.connections.vault, key.org
    )
    return CarrierList(
        carriers=[
            CarrierRow(kind=carrier.account.kind, account=carrier.id, label=carrier.account.label)
            for carrier in carriers_listed
        ]
    )


# Bringing an account the org already holds replaces its secret; a Twilio pair is tried first.
@router.put("/v1/carrier")
async def put_carrier(body: carriers.Account, key: NumbersKey, gateway: GatewayDep) -> CarrierRow:
    """Keep an account of the org: a Twilio account, a SIP peer, a WhatsApp number at Meta."""
    await verify_account(gateway.connections.http, body)
    carrier = await carriers.put_carrier(
        gateway.connections.pool, gateway.connections.vault, key.org, body
    )
    return CarrierRow(kind=carrier.account.kind, account=carrier.id, label=carrier.account.label)


@router.delete("/v1/carrier", status_code=204)
async def drop_carrier(key: NumbersKey, gateway: GatewayDep, account: AccountAsked = None) -> None:
    """Forget an account; its numbers stay routed until each is let go."""
    await carriers.drop_carrier(
        gateway.connections.pool, gateway.connections.vault, key.org, account
    )


@router.get("/v1/numbers")
async def list_numbers(key: NumbersKey, gateway: GatewayDep) -> list[NumberRow]:
    """The org's numbers in the key's world, and the agent each reaches."""
    return [
        NumberRow(route=record.route, origin=record.origin)
        for record in await routes.records_of(gateway.connections.pool, key.org, key.env)
    ]


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


def _org_scope(key: Acting) -> Scope:
    return Scope(key.org, key.env)

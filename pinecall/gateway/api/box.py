"""The box's own settings and floor: providers, admission, mail, brand, routes, fleet, events."""

import asyncio
import time
from collections import Counter
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from pinecall.channels import routes
from pinecall.channels.telephony import carrier_catalog, firewall, numbers, sip
from pinecall.channels.telephony.carrier_catalog import KnownCarrier
from pinecall.channels.telephony.numbers import NumberImport
from pinecall.domain.call import Route
from pinecall.domain.errors import Conflict, NotAvailable, NotFound
from pinecall.domain.names import PRODUCTION, Env
from pinecall.domain.scope import Scope
from pinecall.fleet import worlds
from pinecall.fleet.roster import STALE_AFTER_S
from pinecall.fleet.worlds import Fleets
from pinecall.gateway import _streams
from pinecall.gateway._deps import GatewayDep, OperatorDep, operator, public_url
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._streams import frame, paced, streamed, wants_sse
from pinecall.gateway.api import hosting as hosting_doors
from pinecall.gateway.api.org import mailbox_of
from pinecall.gateway.api.providers import credentials_of, installed_vendor
from pinecall.gateway.api.sso_login import NO_BOX_WIDE
from pinecall.gateway.api.usage import usage_row_response, usage_totals
from pinecall.log import queries
from pinecall.log.reduce import totals_by_org
from pinecall.log.store import DEFAULT_LIMIT, Store
from pinecall.providers import catalog
from pinecall.providers.catalog import Providers
from pinecall.tenancy import (
    admission,
    carrier_networks,
    hosted_running,
    letters,
    mail,
    orgs,
    vault,
)
from pinecall.tenancy.admission import Admission
from pinecall.tenancy.carrier_networks import NetworkState
from pinecall.tenancy.mail import MailboxStatus
from pinecall.wire.frames import Entry
from pinecall.wire.rest.accounts import (
    BrandRow,
    OrgMailRequest,
    SendTestLetterRequest,
    SendTestLetterResponse,
)
from pinecall.wire.rest.hosting import ServedPage
from pinecall.wire.rest.ops import (
    AdmitCarrierRequest,
    BoxCarrier,
    BoxCarriers,
    BoxEvent,
    BoxFence,
    BoxMailResponse,
    BoxNumber,
    BoxProvider,
    BoxSignInResponse,
    CarrierNetworkRow,
    FenceOpening,
    FleetListed,
    NumberCameIn,
    PutBrandRequest,
    PutSignInRequest,
    RouteRequest,
    RouteRow,
)
from pinecall.wire.rest.providers import ProviderKeyRequest, VendorsResponse
from pinecall.wire.rest.usage import BoxUsagePage

router = APIRouter(dependencies=[Depends(operator)])


NOTHING_STORED = (
    "this box has no stored mail server: what it posts through, if anything, is PINECALL_SMTP_URL"
)


NOTHING_TO_TEST = (
    "this box has no mail server: wire one at PUT /v1/ops/mail, or set PINECALL_SMTP_URL"
)


NO_SUCH_ORG = "no org named {named}: by id or by slug"


NO_BOX_KEY = "this box holds no key for {vendor}"


NO_SUCH_ROUTE = "no route for {number} in org {slug}"


NO_SUCH_WORKER = "no worker named {worker} has knocked at this gateway"


GOOGLE_CALLBACK = "/v1/login/google/callback"


# The usage stream polls: a summary is a row of the store, not a topic.
POLL_S = 1.0


# ── what the box runs, and what a new org is given ──


# The console's box screens read and write these rows whole; code holds no list of them.
@router.get("/v1/ops/providers")
async def get_providers(gateway: GatewayDep) -> Providers:
    """The providers row: defaults, models, voices, tuning, rates, the judge, the embedder."""
    return await catalog.providers(gateway.connections.pool)


@router.put("/v1/ops/providers")
async def put_providers(body: Providers, gateway: GatewayDep) -> Providers:
    """The providers row replaced whole; a vendor not installed or not doing its stage refused."""
    await catalog.configure(gateway.connections.pool, body)
    return body


# Offering a vendor is holding its key: the box's credentials, sealed, never read back.
@router.get("/v1/ops/provider-keys")
async def box_vendors(gateway: GatewayDep) -> VendorsResponse:
    """The vendors the box holds a key for, never the key."""
    connections = gateway.connections
    credentials = await vault.box_credentials(connections.pool, connections.vault)
    return VendorsResponse(vendors=sorted(credentials))


@router.put("/v1/ops/provider-keys/{vendor}", status_code=204)
async def put_box_key(vendor: str, body: ProviderKeyRequest, gateway: GatewayDep) -> None:
    """The box's own credentials for a vendor; orgs it lends to run on them from the next call."""
    connections = gateway.connections
    await vault.put_box_credentials(
        connections.pool, connections.vault, installed_vendor(vendor), credentials_of(body)
    )


@router.delete("/v1/ops/provider-keys/{vendor}", status_code=204)
async def drop_box_key(vendor: str, gateway: GatewayDep) -> None:
    """The box stops offering the vendor on its key; 404 when it held none."""
    named = installed_vendor(vendor)
    if not await vault.drop_box_credentials(gateway.connections.pool, named):
        raise NotFound(NO_BOX_KEY.format(vendor=named))


@router.get("/v1/ops/admission")
async def get_admission(gateway: GatewayDep) -> Admission:
    """What a newborn org is given in each world; nothing limited on a box that never said."""
    return await admission.admission(gateway.connections.pool)


# Read at the moment an org is made: the orgs already made keep their quotas.
@router.put("/v1/ops/admission")
async def put_admission(body: Admission, gateway: GatewayDep) -> Admission:
    """What a newborn org is given, replaced whole."""
    await admission.set_admission(gateway.connections.pool, body)
    return body


@router.get("/v1/ops/hosted-usage")
async def box_hosted_usage(
    gateway: GatewayDep, month: Annotated[str | None, Query()] = None
) -> ServedPage:
    """The time every org's apps served, both worlds, per UTC day, one month: what bills them."""
    since, until = hosting_doors.month_of(month)
    rows = await hosted_running.served(gateway.connections.pool, since, until)
    return hosting_doors.served_page(since, until, rows)


@router.get("/v1/ops/fleets")
async def get_fleets(gateway: GatewayDep) -> Fleets:
    """The fleet of workers each world's calls are dispatched to."""
    return await worlds.fleets(gateway.connections.pool)


@router.put("/v1/ops/fleets")
async def put_fleets(body: Fleets, gateway: GatewayDep) -> Fleets:
    """The fleet of each world, replaced whole; the next dispatch reads it."""
    await worlds.set_fleets(gateway.connections.pool, body)
    return body


# ── the box's mailbox ──


@router.get("/v1/ops/mail")
async def get_box_mail(gateway: GatewayDep) -> BoxMailResponse:
    """The mailbox the box posts through, where it came from, and how its last letter went."""
    return _box_mail_row(await _box_mail(gateway))


# Sends nothing: the test door does, and a person watches it.
@router.put("/v1/ops/mail")
async def put_box_mail(body: OrgMailRequest, gateway: GatewayDep) -> BoxMailResponse:
    """The box's mailbox, stored over the environment's; its letters go through it next."""
    connections = gateway.connections
    await mail.put_box_mail(connections.pool, connections.vault, mailbox_of(body))
    return _box_mail_row(await _box_mail(gateway))


@router.delete("/v1/ops/mail", status_code=204)
async def drop_box_mail(gateway: GatewayDep) -> None:
    """Forget the stored mailbox, back to the environment's; 404 when none was stored."""
    if not await mail.drop_box_mail(gateway.connections.pool):
        raise NotFound(NOTHING_STORED)


# The one door of the box that waits on SMTP: a person is watching.
@router.post("/v1/ops/mail/test")
async def send_box_test_letter(
    body: SendTestLetterRequest, gateway: GatewayDep
) -> SendTestLetterResponse:
    """One test letter through the box's mailbox, waited for."""
    to = mail.address_of(body.to)
    if await gateway.outbox.mailbox_for(None) is None:
        raise Conflict(NOTHING_TO_TEST)
    letter = letters.probe_letter(to, await letters.brand_of(gateway.connections.pool))
    error = await gateway.outbox.sent(None, letter)
    return SendTestLetterResponse(sent=error is None, error=error)


# ── sign-in across orgs ──


# Box-wide sign-in is not in this version: the doors stand, say so, and name the URI to register.
@router.get("/v1/ops/signin")
async def get_box_signin(request: Request, gateway: GatewayDep) -> BoxSignInResponse:
    """Every provider the box could offer every org's people: none wired in this version."""
    redirect = f"{public_url(request, gateway)}{GOOGLE_CALLBACK}"
    return BoxSignInResponse(
        google=BoxProvider(configured=False, client_id=None, redirect_uri=redirect)
    )


@router.put("/v1/ops/signin/google")
async def put_google_signin(_body: PutSignInRequest) -> BoxProvider:
    """Refused: box-wide Google sign-in is not in this version."""
    raise NotAvailable(NO_BOX_WIDE)


@router.delete("/v1/ops/signin/google", status_code=204)
async def drop_google_signin() -> None:
    """Refused: box-wide Google sign-in is not in this version."""
    raise NotAvailable(NO_BOX_WIDE)


# ── the brand ──


@router.get("/v1/ops/brand")
async def get_brand(gateway: GatewayDep) -> BrandRow:
    """What the box's letters and sign-in page are called and painted with."""
    return _brand_row(await letters.brand_of(gateway.connections.pool))


@router.put("/v1/ops/brand")
async def put_brand(body: PutBrandRequest, gateway: GatewayDep) -> BrandRow:
    """The brand changed field by field: one left out stays, an empty one goes to the default."""
    pool = gateway.connections.pool
    brand = letters.apply_brand(
        await letters.brand_of(pool), name=body.name, logo_url=body.logo_url, accent=body.accent
    )
    await letters.put_brand(pool, brand)
    return _brand_row(brand)


# ── every org's floor, and every org's meter ──


# Live only, no cursor: frame ids are per-log seqs that interleave, so a reconnect starts now.
@router.get("/v1/ops/events", response_model=None)
async def stream_box_events(gateway: GatewayDep) -> StreamingResponse:
    """Every org's floor at once, each frame saying whose."""
    return streamed(_owned(gateway, await gateway.logs.box_reader()), gateway.closing)


@router.get("/v1/ops/usage", response_model=None)
async def box_usage(
    gateway: GatewayDep,
    after: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=DEFAULT_LIMIT)] = DEFAULT_LIMIT,
    org: Annotated[str | None, Query()] = None,
    accept: Annotated[str | None, Header()] = None,
) -> StreamingResponse | BoxUsagePage:
    """Every org's metered rows after the cursor, totals per org; or the same as a stream."""
    pool, store = gateway.connections.pool, gateway.logs.store
    only = None
    if org is not None:
        found = await orgs.find(pool, org)
        only = org if found is None else found.id
    if wants_sse(accept):
        return streamed(_metered_stream(store, after, only), gateway.closing)
    page = await queries.metered_page(store, after=after, limit=limit, org=only)
    return BoxUsagePage(
        rows=[usage_row_response(row) for row in page.rows],
        totals={org: usage_totals(used) for org, used in totals_by_org(page.rows).items()},
        next=page.next,
    )


# ── the routes ──


@router.get("/v1/ops/routes")
async def list_box_routes(
    gateway: GatewayDep, org: Annotated[str, Query()], env: Annotated[Env, Query()] = PRODUCTION
) -> list[RouteRow]:
    """Every number the org answers at in the world, oldest first."""
    found = await _org_id(gateway, org)
    return [
        _route_row(route) for route in await routes.of_org(gateway.connections.pool, found, env)
    ]


# One row per number per org: a number added again moves. It is admitted on the SFU at once;
# the carrier is never touched, so it rings only where the carrier already points it here.
@router.post("/v1/ops/routes")
async def add_box_route(body: RouteRequest, gateway: GatewayDep) -> RouteRow:
    """A number answered by this org's agent, in this world, on this channel."""
    scope = Scope(org=await _org_id(gateway, body.org), env=body.env)
    wanted = NumberImport(scope, body.agent, body.number, channel=body.channel)
    typed = await numbers.type_route(gateway.connections, wanted)
    return _route_row(typed.route)


@router.delete("/v1/ops/routes/{number}", status_code=204)
async def drop_box_route(number: str, gateway: GatewayDep, org: Annotated[str, Query()]) -> None:
    """The org's route at the number forgotten, and its admission; 404 for a number nobody typed."""
    found = await _org_id(gateway, org)
    route = await routes.of_number(gateway.connections.pool, found, number)
    if route is None:
        raise NotFound(NO_SUCH_ROUTE.format(number=number, slug=org))
    await numbers.release(gateway.connections, found, number, route.env)


# What a number would do if it rang now: whose it is, how it came, and whether anyone picks up.
@router.get("/v1/ops/numbers")
async def list_box_numbers(gateway: GatewayDep) -> list[BoxNumber]:
    """Every number the box answers at, of every org and world, by number."""
    pool = gateway.connections.pool
    slugs = {org.id: org.slug for org in await orgs.listed(pool)}
    found = await routes.on_the_box(pool)
    running = {
        (route.org, route.env): {
            agent.slug
            for agent in gateway.sockets.holding(
                Scope(org=route.org, env=route.env), every_corner=True
            )
        }
        for route in (row.route for row in found)
    }
    return [
        BoxNumber(
            number=row.route.number or "",
            channel=row.route.channel,
            org=slugs.get(row.route.org, row.route.org),
            env=row.route.env,
            agent=row.route.agent,
            came_in=_came_in(row),
            running=row.route.agent in running[(row.route.org, row.route.env)],
            answered_by=None
            if row.answering == row.route.org
            else slugs.get(row.answering, row.answering),
            via=row.via,
        )
        for row in found
    ]


# ── the carriers ──


@router.get("/v1/ops/carriers")
async def list_box_carriers(gateway: GatewayDep) -> BoxCarriers:
    """The catalog, each carrier admitted or not with the numbers it brings, and the fence now."""
    pool = gateway.connections.pool
    admitted = await carrier_catalog.admitted(pool)
    through = Counter(_comes_through(record) for record in await routes.on_the_box(pool))
    applied = await firewall.last_applied(pool)
    return BoxCarriers(
        carriers=[
            _box_carrier(carrier, admitted, through[carrier.kind])
            for carrier in carrier_catalog.known().values()
        ],
        fence=BoxFence(
            openings=[
                FenceOpening(network=opening.network, reason=opening.reason)
                for opening in await firewall.openings(pool)
            ],
            applied_at=None if applied is None else applied.at,
            applied=None if applied is None else applied.networks,
        ),
    )


# On, every org sees it under Add a number and the fence opens to its networks within a minute.
@router.put("/v1/ops/carriers/{kind}")
async def admit_box_carrier(
    kind: str, body: AdmitCarrierRequest, gateway: GatewayDep
) -> BoxCarrier:
    """Admit a carrier of the catalog, or stop admitting it; Twilio is admitted always."""
    pool = gateway.connections.pool
    admitted = await carrier_catalog.admit(pool, kind, on=body.admitted)
    through = Counter(_comes_through(record) for record in await routes.on_the_box(pool))
    return _box_carrier(carrier_catalog.known_carrier(kind), admitted, through[kind])


@router.get("/v1/ops/carrier-networks")
async def list_carrier_networks(
    gateway: GatewayDep, state: Annotated[NetworkState | None, Query()] = None
) -> list[CarrierNetworkRow]:
    """Every network an org asked 5060 to open to, or those in one state, oldest first."""
    pool = gateway.connections.pool
    slugs = {org.id: org.slug for org in await orgs.listed(pool)}
    return [_network_row(ask, slugs) for ask in await carrier_networks.listed(pool, state)]


@router.post("/v1/ops/carrier-networks/{ask}/approve")
async def approve_carrier_network(
    ask: int, gateway: GatewayDep, operating: OperatorDep
) -> CarrierNetworkRow:
    """Open 5060 to the network within a minute, and admit the org's numbers it fences."""
    return await _decided(gateway, ask, "approved", operating)


@router.post("/v1/ops/carrier-networks/{ask}/refuse")
async def refuse_carrier_network(
    ask: int, gateway: GatewayDep, operating: OperatorDep
) -> CarrierNetworkRow:
    """Keep 5060 closed to the network; numbers it alone fenced are let go of on the SFU."""
    return await _decided(gateway, ask, "refused", operating)


# ── the fleet ──


@router.get("/v1/ops/fleet")
async def list_fleet(gateway: GatewayDep) -> FleetListed:
    """Every worker heard from, of both fleets, and each fleet summed over the ones up."""
    now = time.time()
    named = await worlds.fleets(gateway.connections.pool)
    fleets = list(dict.fromkeys((named.production, named.sandbox)))
    return FleetListed(
        now=now,
        stale_after_s=STALE_AFTER_S,
        workers=[seat for fleet in fleets for seat in gateway.roster.of(fleet, now)],
        totals=[gateway.roster.totals(fleet, now) for fleet in fleets],
    )


# A cordon is told on the worker's next heartbeat: it takes no new call, finishes, and leaves.
@router.post("/v1/ops/fleet/{worker}/cordon", status_code=204)
async def cordon_worker(
    worker: str, gateway: GatewayDep, fleet: Annotated[str | None, Query()] = None
) -> None:
    """Cordon a worker of a fleet; the fleet is found by the worker's name when not named."""
    if not gateway.roster.cordon(_fleet_of(gateway, fleet, worker), worker):
        raise NotFound(NO_SUCH_WORKER.format(worker=worker))


@router.delete("/v1/ops/fleet/{worker}/cordon", status_code=204)
async def uncordon_worker(
    worker: str, gateway: GatewayDep, fleet: Annotated[str | None, Query()] = None
) -> None:
    """Take a worker's cordon back, when it has not left yet."""
    if not gateway.roster.cordon(_fleet_of(gateway, fleet, worker), worker, on=False):
        raise NotFound(NO_SUCH_WORKER.format(worker=worker))


def _box_mail_row(kept: MailboxStatus | None) -> BoxMailResponse:
    """The box's mailbox as the doors send it; the password never."""
    return BoxMailResponse.model_validate(
        {
            "configured": kept is not None,
            "source": None if kept is None else kept.source,
            "host": None if kept is None else kept.mailbox.host,
            "port": None if kept is None else kept.mailbox.port,
            "security": None if kept is None else kept.mailbox.security,
            "username": None if kept is None else kept.mailbox.username,
            "from": None if kept is None else kept.mailbox.sender,
            "verified_at": None
            if kept is None or kept.verified_at is None
            else kept.verified_at.isoformat(),
            "last_error": None if kept is None else kept.last_error,
        }
    )


def _brand_row(brand: letters.Brand) -> BrandRow:
    """The brand as the doors send it."""
    return BrandRow(name=brand.name, logo_url=brand.logo_url, accent=brand.accent)


def _route_row(route: Route) -> RouteRow:
    """A route as the operator lists it."""
    return RouteRow(
        org=route.org,
        number=route.number or "",
        agent=route.agent,
        channel=route.channel,
        env=route.env,
        managed=route.managed,
    )


def _came_in(row: routes.RouteRecord) -> NumberCameIn:
    """How the number reached the box: the kind of the org's account it came from, or its origin."""
    match row.origin, row.carrier:
        case "imported", "twilio" | "sip" | "whatsapp" as kind:
            return kind
        case origin, _:
            return origin


def _box_carrier(carrier: KnownCarrier, admitted: frozenset[str], numbers: int) -> BoxCarrier:
    """A carrier of the catalog as the doors send it."""
    return BoxCarrier(
        kind=carrier.kind,
        name=carrier.name,
        control=carrier.control,
        networks=list(carrier.networks),
        source=carrier.source,
        read_on=carrier.read_on.isoformat(),
        admitted=carrier.kind in admitted,
        fixed=carrier.kind == carrier_catalog.BOX_CARRIER,
        numbers=numbers,
    )


# A number with no account, no carrier named and no networks of its own is fenced to Twilio's.
def _comes_through(record: routes.RouteRecord) -> str | None:
    """The catalog carrier a number reaches the box through, if any."""
    if record.via is not None:
        return record.via
    if record.carrier is not None:
        return record.carrier
    if record.route.managed or record.origin in {"hooked", "typed"}:
        return carrier_catalog.BOX_CARRIER
    return None


def _network_row(ask: carrier_networks.NetworkAsk, slugs: dict[str, str]) -> CarrierNetworkRow:
    """A network asked for as the doors send it, the org by its slug."""
    return CarrierNetworkRow(
        id=ask.id,
        org=slugs.get(ask.org, ask.org),
        source=ask.source,
        network=ask.network,
        state=ask.state,
        asked_at=ask.asked_at,
        decided_by=ask.decided_by,
        decided_at=ask.decided_at,
    )


async def _decided(
    gateway: Gateway, ask: int, state: NetworkState, operating: str
) -> CarrierNetworkRow:
    """The operator's answer kept, and the org's trunks made to follow it."""
    pool = gateway.connections.pool
    decided = await carrier_networks.decide(pool, ask, state, operating)
    await sip.readmit(gateway.connections, decided.org)
    slugs = {org.id: org.slug for org in await orgs.listed(pool)}
    return _network_row(decided, slugs)


async def _box_mail(gateway: Gateway) -> MailboxStatus | None:
    connections = gateway.connections
    return await mail.box_mail_of(connections.pool, connections.vault, gateway.outbox.environment)


async def _org_id(gateway: Gateway, named: str) -> str:
    org = await orgs.find(gateway.connections.pool, named)
    if org is None:
        raise NotFound(NO_SUCH_ORG.format(named=named))
    return org.id


def _fleet_of(gateway: Gateway, fleet: str | None, worker: str) -> str:
    if fleet is not None:
        return fleet
    return next((named for named, name in gateway.roster.seats if name == worker), "")


# Entries whose log nobody claimed yet carry no org, and are left out.
async def _owned(gateway: Gateway, entries: AsyncIterator[Entry]) -> AsyncIterator[str]:
    yield f"retry: {_streams.RETRY_MS}\n\n"
    async for entry in paced(entries):
        if entry is None:
            yield _streams.PING
            continue
        claimant = await gateway.logs.claimant_of(entry)
        if claimant is None:
            continue
        event = BoxEvent(org=claimant.org, env=claimant.env, entry=entry)
        yield frame(entry.type, event.written(), seq=entry.seq)


async def _metered_stream(store: Store, after: int, only: str | None) -> AsyncIterator[str]:
    yield f"retry: {_streams.RETRY_MS}\n\n"
    cursor = after
    while True:
        page = await queries.metered_page(store, after=cursor, limit=DEFAULT_LIMIT, org=only)
        for row in page.rows:
            yield frame("usage", usage_row_response(row).written(), seq=row.cursor)
        if page.next is not None:
            cursor = page.next
            continue
        yield _streams.PING
        await asyncio.sleep(POLL_S)

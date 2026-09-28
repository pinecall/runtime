"""The box's own settings and floor: mailbox, brand, sign-in, routes, fleet, events, usage."""

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse

from pinecall.channels import routes
from pinecall.domain.call import Route
from pinecall.domain.errors import Conflict, NotAvailable, NotFound
from pinecall.domain.names import PRODUCTION, Env
from pinecall.fleet import worlds
from pinecall.fleet.roster import STALE_AFTER_S
from pinecall.gateway import _streams
from pinecall.gateway._deps import GatewayDep, operator, public_url
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._streams import frame, paced, streamed, wants_sse
from pinecall.gateway.api.org import mailbox_of
from pinecall.gateway.api.sso_login import NO_BOX_WIDE
from pinecall.gateway.api.usage import usage_row_response, usage_totals
from pinecall.log import queries
from pinecall.log.reduce import totals_by_org
from pinecall.log.store import DEFAULT_LIMIT, Store
from pinecall.tenancy import letters, mail, orgs
from pinecall.tenancy.mail import MailboxStatus
from pinecall.wire.frames import Entry
from pinecall.wire.rest.accounts import (
    BrandRow,
    OrgMailRequest,
    SendTestLetterRequest,
    SendTestLetterResponse,
)
from pinecall.wire.rest.ops import (
    BoxEvent,
    BoxMailResponse,
    BoxProvider,
    BoxSignInResponse,
    FleetListed,
    PutBrandRequest,
    PutSignInRequest,
    RouteRequest,
    RouteRow,
)
from pinecall.wire.rest.usage import BoxUsagePage

router = APIRouter(dependencies=[Depends(operator)])


NOTHING_STORED = (
    "this box has no stored mail server: what it posts through, if anything, is PINECALL_SMTP_URL"
)


NOTHING_TO_TEST = (
    "this box has no mail server: wire one at PUT /v1/ops/mail, or set PINECALL_SMTP_URL"
)


NO_SUCH_ORG = "no org named {named}: by id or by slug"


NO_SUCH_ROUTE = "no route for {number} in org {slug}"


NO_SUCH_WORKER = "no worker named {worker} has knocked at this gateway"


GOOGLE_CALLBACK = "/v1/login/google/callback"


# The usage stream polls: a summary is a row of the store, not a topic.
POLL_S = 1.0


# ── the box's mailbox ──


@router.get("/v1/ops/mail")
async def get_box_mail(gateway: GatewayDep) -> BoxMailResponse:
    """The mailbox the box posts through, where it came from, and how its last letter went."""
    return box_mail_row(await _box_mail(gateway))


# Sends nothing: the test door does, and a person watches it.
@router.put("/v1/ops/mail")
async def put_box_mail(body: OrgMailRequest, gateway: GatewayDep) -> BoxMailResponse:
    """The box's mailbox, stored over the environment's; its letters go through it next."""
    connections = gateway.connections
    await mail.put_box_mail(connections.pool, connections.vault, mailbox_of(body))
    return box_mail_row(await _box_mail(gateway))


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
async def put_google_signin(body: PutSignInRequest, gateway: GatewayDep) -> BoxProvider:
    """Refused: box-wide Google sign-in is not in this version."""
    del body, gateway
    raise NotAvailable(NO_BOX_WIDE)


@router.delete("/v1/ops/signin/google", status_code=204)
async def drop_google_signin(gateway: GatewayDep) -> None:
    """Refused: box-wide Google sign-in is not in this version."""
    del gateway
    raise NotAvailable(NO_BOX_WIDE)


# ── the brand ──


@router.get("/v1/ops/brand")
async def get_brand(gateway: GatewayDep) -> BrandRow:
    """What the box's letters and sign-in page are called and painted with."""
    return brand_row(await letters.brand_of(gateway.connections.pool))


@router.put("/v1/ops/brand")
async def put_brand(body: PutBrandRequest, gateway: GatewayDep) -> BrandRow:
    """The brand changed field by field: one left out stays, an empty one goes to the default."""
    pool = gateway.connections.pool
    brand = letters.apply_brand(
        await letters.brand_of(pool), name=body.name, logo_url=body.logo_url, accent=body.accent
    )
    await letters.put_brand(pool, brand)
    return brand_row(brand)


# ── every org's floor, and every org's meter ──


# Live only, no cursor: frame ids are per-log seqs that interleave, so a reconnect starts now.
@router.get("/v1/ops/events", response_model=None)
async def stream_box_events(gateway: GatewayDep) -> StreamingResponse:
    """Every org's floor at once, each frame saying whose."""
    return streamed(_owned(gateway, gateway.logs.box.subscribe()), gateway.closing)


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
    return [route_row(route) for route in await routes.of_org(gateway.connections.pool, found, env)]


# One row per number per org: a number added again moves.
@router.post("/v1/ops/routes")
async def add_box_route(body: RouteRequest, gateway: GatewayDep) -> RouteRow:
    """A number answered by this org's agent, in this world, on this channel."""
    route = Route(
        org=await _org_id(gateway, body.org),
        agent=body.agent,
        channel=body.channel,
        number=body.number,
        env=body.env,
    )
    await routes.put(gateway.connections.pool, route, account=None)
    return route_row(route)


@router.delete("/v1/ops/routes/{number}", status_code=204)
async def drop_box_route(number: str, gateway: GatewayDep, org: Annotated[str, Query()]) -> None:
    """The org's route at the number forgotten; 404 for a number nobody typed."""
    found = await _org_id(gateway, org)
    if not await routes.remove(gateway.connections.pool, found, number):
        raise NotFound(NO_SUCH_ROUTE.format(number=number, slug=org))


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


def box_mail_row(kept: MailboxStatus | None) -> BoxMailResponse:
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


def brand_row(brand: letters.Brand) -> BrandRow:
    """The brand as the doors send it."""
    return BrandRow(name=brand.name, logo_url=brand.logo_url, accent=brand.accent)


def route_row(route: Route) -> RouteRow:
    """A route as the operator lists it."""
    return RouteRow(
        org=route.org,
        number=route.number or "",
        agent=route.agent,
        channel=route.channel,
        env=route.env,
        managed=route.managed,
    )


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
        org = await gateway.logs.store.owner(entry.call, entry.agent)
        if org is not None:
            yield frame(entry.type, BoxEvent(org=org, entry=entry).written(), seq=entry.seq)


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

"""A visitor's doors: a browser's room token, and the code a caller keys to tie a page."""

import time
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from pinecall.channels import rooms, routes
from pinecall.channels.rooms import Dispatch
from pinecall.domain.call import new_call_id
from pinecall.domain.errors import (
    Conflict,
    DeclarationRefused,
    NotAllowed,
    NotAvailable,
    NotFound,
)
from pinecall.domain.names import THE_WIDGET
from pinecall.domain.person import RoomScope
from pinecall.fleet import worlds
from pinecall.gateway import _deps
from pinecall.gateway._deps import GatewayDep, ReaderDep, TalkKey
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._served import (
    NO_AGENT,
)
from pinecall.tenancy import keys, tokens
from pinecall.tenancy.keys import LONGEST_VISIT_TTL_S
from pinecall.tenancy.tokens import MINTED_FOR_A_VISIT, ONE_VISIT_TTL_S
from pinecall.wire.events import (
    FleetFull,
)
from pinecall.wire.rest.calls import (
    CodeStatus,
    IssueCodeRequest,
    IssueCodeResponse,
    MintTokenRequest,
    MintTokenResponse,
)

router = APIRouter()


NOT_A_VISIT = "scope is one of {scopes}: observe and supervise take the API key instead"


NO_AGENT_NAMED = "name the agent: `agent` in the body, or agentName as a livekit client sends it"


OURS_TO_SET = {
    "room_name": "the room is the call id this door mints",
    "participant_name": "a name is PII, and the log would carry it",
    "participant_metadata": "it carries the contact id: send `contact`",
}


NOT_YOURS_TO_SET = "{field} is minted here and refused in the body: {why}"


OUR_ATTRIBUTES = "pinecall."


FLEET_FULL = (
    "every seat of the fleet is taken: {active} calls on {workers} workers. Offer a call back, or "
    "try again in a minute"
)


NO_PHONE = "agent {slug} answers at no phone number in {env}: import one first"


NOT_YOUR_CODE = "this token reads another code"


# 25 s: under the 30 s a proxy lets a request idle.
LONGEST_WAIT_S = 25.0


A_PAGE_MAY_ASK_AFTER_S = 60.0


class CodeQuery(BaseModel):
    """Whether a page's ask waits for its code to be keyed."""

    wait: bool = False


# 201, livekit's own: a client SDK's token source expects it. The ledger row is written first,
# so the dispatch spends the token once.
@router.post("/v1/tokens", status_code=201)
async def mint_room_token(
    body: MintTokenRequest, key: TalkKey, gateway: GatewayDep
) -> MintTokenResponse:
    """A room token for one of the key's agents, the dispatch to its world's fleet inside it."""
    _refuse_what_is_ours(body)
    org_scope = keys.scope_of(key.bearer, key.env)
    agent = body.agent or rooms.client_named_agent(body.room_config)
    if agent is None:
        raise DeclarationRefused(NO_AGENT_NAMED)
    if gateway.sockets.serving(org_scope, agent, None) is None:
        raise NotFound(NO_AGENT.format(slug=agent))
    fleet = worlds.fleet_of(await worlds.fleets(gateway.connections.pool), org_scope.env)
    await _room_for_one_more(gateway, fleet, agent)
    await _deps.admit_call(gateway, org_scope, agent)
    call = new_call_id()
    ttl = min(body.ttl_s or ONE_VISIT_TTL_S, LONGEST_VISIT_TTL_S)
    expires_at = time.time() + ttl
    scope = _visit_scope(body.scope)
    visitor = body.participant_identity or f"web_{new_call_id()[5:17]}"
    carried = Dispatch(
        agent=agent,
        org=org_scope.org,
        env=org_scope.env,
        holder=org_scope.holder or None,
        scope=scope,
        caller=visitor,
        contact=body.contact,
        metadata=body.metadata,
    )
    token = tokens.room_token(
        gateway.signer,
        call,
        scope,
        tokens.Visitor(
            expires_at=expires_at,
            identity=visitor,
            dispatch=rooms.room_dispatch(fleet, carried),
        ),
    )
    await tokens.minted(
        gateway.connections.pool, tokens.MintedToken(call, org_scope.org, agent, scope, expires_at)
    )
    return MintTokenResponse(
        server_url=gateway.connections.settings.livekit_public_url
        or gateway.connections.settings.livekit_url,
        participant_token=token,
        call=call,
        log_token=tokens.log_token(gateway.signer, call, body.log),
    )


# The number is the key's own agent's in the key's world: a key issues codes for its agents.
@router.post("/v1/codes", status_code=201)
async def issue_code(
    body: IssueCodeRequest, key: TalkKey, gateway: GatewayDep
) -> IssueCodeResponse:
    """Four digits for a caller to key, the number to call, and a token that asks after them."""
    doors = await routes.of_org(gateway.connections.pool, key.org, key.env)
    number = next(
        (door.number for door in doors if door.agent == body.agent and door.channel == "phone"),
        None,
    )
    if number is None:
        raise Conflict(NO_PHONE.format(slug=body.agent, env=key.env))
    issued = await gateway.codes.issue(key.env, body.agent, body.ttl_s, body.log)
    # The token outlives the code a little, so the page is told it expired rather than refused.
    asks_until = issued.expires_at + A_PAGE_MAY_ASK_AFTER_S
    token = tokens.code_token(gateway.signer, issued.code, body.agent, key.env, asks_until)
    return IssueCodeResponse(
        code=issued.code, number=number, expires_at=issued.expires_at, code_token=token
    )


# A page holds no key: the code token its page was handed is the only credential.
@router.get("/v1/codes/{code}")
async def code_status(
    code: str, reading: ReaderDep, gateway: GatewayDep, query: Annotated[CodeQuery, Query()]
) -> CodeStatus:
    """How the code stands; with ?wait=1, held up to 25 s for a call to key it."""
    visit = reading.visit
    if visit is None or visit.code != code or visit.agent is None or visit.env is None:
        raise NotAllowed(NOT_YOUR_CODE)
    issued = await gateway.codes.status_of(visit.env, visit.agent, code)
    if issued is None:
        raise NotFound(_deps.NOBODY_ISSUED.format(code=code, agent=visit.agent))
    left = issued.expires_at - time.time()
    if query.wait and issued.claimed is None and left > 0:
        issued = await gateway.codes.waited(issued, min(LONGEST_WAIT_S, left))
    if issued.claimed is not None:
        token = tokens.log_token(gateway.signer, issued.claimed, issued.log)
        return CodeStatus(
            code=code,
            status="claimed",
            expires_at=issued.expires_at,
            call=issued.claimed,
            log_token=token,
        )
    status = "expired" if time.time() >= issued.expires_at else "waiting"
    return CodeStatus(
        code=code, status=status, expires_at=issued.expires_at, call=None, log_token=None
    )


# A gateway nobody's worker reported to refuses nobody.
async def _room_for_one_more(gateway: Gateway, fleet: str, agent: str) -> None:
    totals = gateway.roster.totals(fleet, time.time())
    if not totals.full:
        return
    full = FleetFull(channel=THE_WIDGET, workers=totals.workers, active=totals.active)
    await gateway.logs.agent(agent).append("fleet.full", full.written())
    raise NotAvailable(FLEET_FULL.format(active=totals.active, workers=totals.workers))


def _refuse_what_is_ours(body: MintTokenRequest) -> None:
    ours = (
        ("room_name", body.room_name),
        ("participant_name", body.participant_name),
        ("participant_metadata", body.participant_metadata),
    )
    for field, sent in ours:
        if sent is not None:
            raise DeclarationRefused(NOT_YOURS_TO_SET.format(field=field, why=OURS_TO_SET[field]))
    if any(name.startswith(OUR_ATTRIBUTES) for name in body.participant_attributes):
        raise DeclarationRefused(f"participant_attributes under {OUR_ATTRIBUTES} are minted here")


def _visit_scope(scope: str) -> RoomScope:
    for visit in MINTED_FOR_A_VISIT:
        if visit == scope:
            return visit
    raise DeclarationRefused(NOT_A_VISIT.format(scopes=sorted(MINTED_FOR_A_VISIT)))

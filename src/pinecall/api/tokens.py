"""POST /v1/tokens: LiveKit's token endpoint, minted only for an agent the key's org answers."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import Field

from pinecall.api._deps import (
    AdmissionDep,
    FleetDep,
    KeyDep,
    LogsDep,
    RoutesDep,
    SettingsDep,
    TokensDep,
)
from pinecall.api._serving import ServingDep
from pinecall.api.agents.registry import NO_AGENT, RegistryDep
from pinecall.auth.keys import KeyRecord
from pinecall.auth.scopes import a_room_token, a_visitor, secret_for
from pinecall.orgs.admission import QuotaExhausted
from pinecall.routes import answering
from pinecall.tokens.ledger import TokenRecord
from pinecall.tokens.room import a_dispatch, the_agent_a_client_named
from pinecall.types import THE_WIDGET, DeclarationRefused, a_call_id
from pinecall.types.token import LONGEST_VISIT_TTL_S, MINTED_FOR_A_VISIT, ONE_VISIT_TTL_S
from pinecall_protocol import WireModel, encode
from pinecall_protocol.events import FleetFull

router = APIRouter()

# LiveKit's endpoint answers 201 Created (frontends/build/authentication/endpoint), and every
# client SDK's TokenSource reads that.
CREATED = 201

# The scopes this door mints, in the sentence a wrong one is refused with.
NOT_A_VISIT_SCOPE = "scope is one of {scopes}: observe and supervise take the API key instead"

# A body that named no agent either way. The two spellings are the two callers: a tenant's
# backend writes `agent`, a stock livekit-client writes `agentName` and the SDK packages it.
NO_AGENT_NAMED = "name the agent: `agent` in the body, or agentName as a livekit-client sends it"

# "For fields clients aren't allowed to set, return a 4xx status code" — the LiveKit page's own
# rule. The room is the call, minted here; a name is PII and the log would carry it; the
# participant's metadata is the contact id and nothing else, which `contact` puts there.
NOT_YOURS_TO_SET = "{field} is minted here and is refused in the body: {why}"
REFUSED_FIELDS: dict[str, str] = {
    "room_name": "the room is the call id this door mints",
    "participant_name": "a name is PII, and the log would carry it",
    "participant_metadata": "it carries the contact id; send `contact`",
}

# Our attributes are ours: a body may not pre-fill the scope a token was minted with.
OUR_ATTRIBUTES = "pinecall."
NOT_YOUR_ATTRIBUTE = "participant_attributes under {prefix} are minted here"

# Every worker is full: a token minted now would open a room nobody joins. 503, with the numbers
# and the door that takes a number instead, and `fleet.full` in the agent's log before it — the
# same shape as credits.exhausted. A gateway no worker has knocked at refuses nothing here.
FLEET_FULL = (
    "every seat of the fleet is taken: {active} calls on {workers} workers. Offer a call back — "
    "POST /v1/callbacks with the number — or try again in a minute."
)


class Wanted(WireModel):
    """LiveKit's request body, plus our three: which agent, whose contact, and what is sealed."""

    # ── ours ─────────────────────────────────────────────────────────────────
    agent: str | None = None
    scope: str = "talk"
    # The org's opaque id for the person, or nothing: never a phone number, never a name.
    contact: str | None = None
    # What the tenant's backend seals into the call: it reaches the worker inside the signed
    # dispatch, and a browser can read it there and alter none of it.
    metadata: dict[str, Any] = Field(default_factory=dict)
    ttl_s: int = Field(default=ONE_VISIT_TTL_S, ge=1, le=LONGEST_VISIT_TTL_S)
    # ── LiveKit's ────────────────────────────────────────────────────────────
    participant_identity: str | None = None
    participant_attributes: dict[str, str] = Field(default_factory=dict)
    room_config: dict[str, Any] | None = None
    # Refused when present, by the page's own rule; declared so the refusal is a sentence
    # and not a 422 about an unknown key.
    room_name: str | None = None
    participant_name: str | None = None
    participant_metadata: str | None = None


# The four things in front of LiveKit's mint, in order: the key's org answers this agent on the
# web (404 in the config door's own words), the body sets nothing that is ours to set (400), the
# org's quotas admit one more call (429, and credits.exhausted in the agent's log), and the token
# is written into the ledger before it leaves, so the dispatch can spend it once.
@router.post("/v1/tokens", status_code=CREATED)
async def mint(
    said: Wanted,
    key: KeyDep,
    registry: RegistryDep,
    table: RoutesDep,
    tokens: TokensDep,
    settings: SettingsDep,
    admission: AdmissionDep,
    live: ServingDep,
    fleet: FleetDep,
    logs: LogsDep,
) -> dict[str, Any]:
    """LiveKit's token endpoint: {server_url, participant_token}, plus the call it opens."""
    _refuse_what_is_ours_to_set(said)
    agent = await _the_agent_the_org_answers(said, key, registry, table)
    await _refuse_a_full_fleet(fleet, logs, agent)
    try:
        await admission.a_call(key.org, agent, live.running(key.org))
    except QuotaExhausted as refused:
        raise HTTPException(429, str(refused)) from refused
    call = a_call_id()
    visitor = said.participant_identity or a_visitor()
    expires_at = time.time() + said.ttl_s
    token = a_room_token(
        call,
        said.scope,
        expires_at,
        secret_for(settings),
        visitor,
        metadata=said.contact or "",
        attributes=said.participant_attributes,
        room_config=a_dispatch(agent, said.scope, visitor, said.metadata),
    )
    await tokens.minted(
        TokenRecord(
            call=call,
            org=key.org,
            agent=agent,
            scope=said.scope,
            expires_at=expires_at,
        )
    )
    return {
        "server_url": settings.livekit_public_url or settings.livekit_url,
        "participant_token": token,
        "call": call,
    }


async def _refuse_a_full_fleet(fleet: FleetDep, logs: LogsDep, agent: str) -> None:
    """503 when no worker can take one more call, written into the agent's log first."""
    totals = fleet.totals(time.time())
    if not totals.full:
        return
    event = FleetFull(channel=THE_WIDGET, workers=totals.workers, active=totals.active)
    await logs.writing_agent(agent).append("fleet.full", encode(event))
    raise HTTPException(503, FLEET_FULL.format(active=totals.active, workers=totals.workers))


def _refuse_what_is_ours_to_set(said: Wanted) -> None:
    """400 for a scope this door does not mint, and for any field the page says is ours."""
    if said.scope not in MINTED_FOR_A_VISIT:
        raise HTTPException(400, NOT_A_VISIT_SCOPE.format(scopes=sorted(MINTED_FOR_A_VISIT)))
    for field, why in REFUSED_FIELDS.items():
        if getattr(said, field) is not None:
            raise HTTPException(400, NOT_YOURS_TO_SET.format(field=field, why=why))
    if any(name.startswith(OUR_ATTRIBUTES) for name in said.participant_attributes):
        raise HTTPException(400, NOT_YOUR_ATTRIBUTE.format(prefix=OUR_ATTRIBUTES))


# Whether the org answers this agent on the web is asked of the very tables the worker will ask
# when the job arrives (GET /v1/routes): a token for a door nobody answers is a call that dies
# after the browser joined, so it is refused before, in the words the config door uses.
async def _the_agent_the_org_answers(
    said: Wanted, key: KeyRecord, registry: RegistryDep, table: RoutesDep
) -> str:
    """The agent the body names, if this key's org answers it on the web. 400 or 404 if not."""
    try:
        agent = said.agent or the_agent_a_client_named(said.room_config)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    if agent is None:
        raise HTTPException(400, NO_AGENT_NAMED)
    answered = await answering.answered(key.org, key.env, registry, table)
    web_doors = (one.route for one in answered if one.route.channel == THE_WIDGET)
    if not any(route.agent == agent for route in web_doors):
        raise HTTPException(404, NO_AGENT.format(slug=agent))
    return agent

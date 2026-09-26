"""The codes a page shows beside the agent's number: issued, asked after, and claimed by a call."""

from __future__ import annotations

import time
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field
from starlette.requests import HTTPConnection
from starlette.status import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from pinecall._settings import Settings
from pinecall.api._deps import (
    AppKeyDep,
    CodesDep,
    KeysDep,
    RoutesDep,
    SettingsDep,
    TalkKeyDep,
)
from pinecall.api._live import LiveDep, Served
from pinecall.api.calls.sink import reading
from pinecall.api.calls.worker_doors import NOT_OPEN, NOT_THIS_ORG
from pinecall.auth.keys import KeyRecord, is_the_fleets
from pinecall.auth.scopes import a_code_token, a_log_token, secret_for
from pinecall.orgs.codes import Codes, Issued
from pinecall.routes.table import Routes
from pinecall_protocol import WireModel, encode
from pinecall_protocol.commands import CallClaim
from pinecall_protocol.defs import Projection
from pinecall_protocol.events import CallClaimed
from pinecall_protocol.rest import Code, CodeStanding

router = APIRouter()


# docs/protocol/codes.md: ten minutes unless the tenant's server says otherwise, never under one
# nor over thirty; and a page that asks to wait is held at most 25 s — under the 30 s a proxy
# gives an idle request — then answered as the code stands, and asks again.
TTL_S = 600
SHORTEST_TTL_S = 60
LONGEST_TTL_S = 1800
WAIT_S = 25.0

# How a code reached a call: the caller keyed it, or the agent heard it said and claimed it.
type Via = Literal["keypad", "agent"]

NO_PHONE = "agent {slug} answers at no phone number in {env}: pinecall numbers import"
NOBODY_ISSUED = (
    "no code {code} is waiting for agent {agent}: nobody issued it, it expired, or a call took it"
)
NOT_YOUR_CODE = "this token does not read that code"
UNREAD = "a code is read with the code token its page was handed"


class Wanted(WireModel):
    """What a tenant's server asks a code for: whose agent, for how long, read how."""

    agent: str
    ttl_s: int = Field(default=TTL_S, ge=SHORTEST_TTL_S, le=LONGEST_TTL_S)
    # What the call's log_token reads once a call claims the code, as on POST /v1/tokens.
    log: Projection = "public"


# The door mints, as the token door does, and says 201 the way LiveKit's endpoint does.
# The number is the agent's phone door in the key's own world, so a key can only issue a code for
# an agent of its own org — and a code for an agent nobody can call is refused before it exists.
@router.post("/v1/codes", status_code=HTTP_201_CREATED)
async def issued(
    said: Wanted, key: TalkKeyDep, routes: RoutesDep, codes: CodesDep, settings: SettingsDep
) -> Code:
    """Four digits, the number to call and key them at, and the token that asks after them."""
    number = await _the_phone_of(routes, key, said.agent)
    issued = await codes.issue(key.env, said.agent, said.ttl_s, said.log)
    token = a_code_token(issued.code, said.agent, key.env, issued.expires_at, secret_for(settings))
    return Code(code=issued.code, number=number, expires_at=issued.expires_at, code_token=token)


# The code token is the only bearer: it names the code, the agent and the world, and reads nothing
# else. A key has no business here — the page asking is a browser, and it holds no key.
@router.get("/v1/codes/{code}")
async def standing(
    code: str,
    connection: HTTPConnection,
    keys: KeysDep,
    settings: SettingsDep,
    codes: CodesDep,
    token: Annotated[str | None, Query()] = None,
    wait: Annotated[bool, Query()] = False,
) -> CodeStanding:
    """Waiting, claimed with the call and a token that reads it, or expired — as the page draws."""
    reader = await reading(connection, keys, settings, token)
    if reader is None:
        raise HTTPException(401, UNREAD, {"WWW-Authenticate": "Bearer"})
    if reader.code != code or reader.agent is None or reader.env is None:
        raise HTTPException(403, NOT_YOUR_CODE)
    issued = await codes.standing(reader.env, reader.agent, code)
    if issued is None:
        raise HTTPException(404, NOBODY_ISSUED.format(code=code, agent=reader.agent))
    now = time.time()
    if wait and issued.claimed is None and not issued.expired(now):
        issued = await codes.waited(issued, min(WAIT_S, issued.expires_at - now))
    return _as_it_stands(issued, settings)


# The worker's door: four tones close together, asked. 404 is the ordinary answer — the caller was
# keying an extension — and it is the same 404 a call this gateway forgot answers, which is what
# has the worker reopen the call and ask once more (worker/client.py).
@router.post("/v1/calls/{call}/claim", status_code=HTTP_204_NO_CONTENT)
async def keyed(call: str, said: CallClaim, key: AppKeyDep, live: LiveDep, codes: CodesDep) -> None:
    """The caller keyed a code: this call is the one its page was waiting for, or 404."""
    served = live.served(call)
    if served is None:
        raise HTTPException(404, NOT_OPEN.format(call=call))
    _refuse_another_orgs(key, served)
    if not await claimed(codes, served, call, said.code, "keypad"):
        raise HTTPException(404, NOBODY_ISSUED.format(code=said.code, agent=served.agent))


# One claim, from the keypad or from the agent: the code is taken on the agent's log, and the call
# says so on its own — the agent's class learns the person is on the site, the console shows it.
async def claimed(codes: Codes, served: Served, call: str, code: str, via: Via) -> bool:
    """Bind the call to the code, call.claimed on its log; False when no page waits on the code."""
    issued = await codes.claim(served.context.env, served.agent, code, call)
    if issued is None:
        return False
    await served.log.append("call.claimed", encode(CallClaimed(code=code, via=via)))
    return True


def _as_it_stands(issued: Issued, settings: Settings) -> CodeStanding:
    """The wire's shape: the call and a fresh token to read it once claimed, nulls until then."""
    if issued.claimed is not None:
        return CodeStanding(
            code=issued.code,
            status="claimed",
            expires_at=issued.expires_at,
            call=issued.claimed,
            log_token=a_log_token(issued.claimed, issued.log, secret_for(settings)),
        )
    return CodeStanding(
        code=issued.code,
        status="expired" if issued.expired(time.time()) else "waiting",
        expires_at=issued.expires_at,
        call=None,
        log_token=None,
    )


async def _the_phone_of(routes: Routes, key: KeyRecord, slug: str) -> str:
    """The first number this agent answers the phone at in the key's world, or 409."""
    doors = await routes.of_org(key.org, key.env)
    for door in doors:
        if door.agent == slug and door.channel == "phone" and door.number is not None:
            return door.number
    raise HTTPException(409, NO_PHONE.format(slug=slug, env=key.env))


def _refuse_another_orgs(key: KeyRecord, served: Served) -> None:
    """403 when a tenant's worker asks about a call of another org; the fleet's asks for all."""
    if not is_the_fleets(key) and served.org != key.org:
        raise HTTPException(403, NOT_THIS_ORG)

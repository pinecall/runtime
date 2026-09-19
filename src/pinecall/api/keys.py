"""/v1/keys: the org's tokens — a person's own, and its servers' — made, listed, revoked."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from pinecall.api._deps import AppKeyDep, KeyDep, KeysDep, MembersDep
from pinecall.auth.corner import author_of
from pinecall.auth.keys import KeyRecord, ListedKey
from pinecall.auth.world import a_person, opens_production
from pinecall.types import DeclarationRefused, an_env, is_a_deployment
from pinecall_protocol import WireModel

# The tenant's own three doors, on the org's API key, exactly as every other tenant door. They
# name no org: an API key IS its org, and a door that took the name would be a door that could
# mint a key into somebody else's. The operator's own are /v1/ops/orgs/{org}/keys (api/orgs.py).
router = APIRouter()

# What a server does, and so what its token opens: it holds the agent over the app socket, reads
# the calls it answers, mints the room tokens its own web page hands a browser, pushes the
# knowledge base in its release step, and runs what the console asks of the agent's directory —
# a simulation, a suite — which the evals doors answer: the production console's Simulations had
# nothing to run on without it (2026-09-19). Nothing about the org's people, numbers or money.
SERVER_SCOPES: frozenset[str] = frozenset({"app", "calls", "talk", "knowledge", "evals"})

# A server's token is made by a PERSON, from the console, and belongs to the org: it names who
# made it and outlives them — a production that stopped when its developer left would be an
# outage nobody chose (0039). Production's is made by somebody the org lets act there.
BY_A_PERSON = "a server's token is made by a person, from Tokens in the console"
NOT_IN_PRODUCTION = "{name} has no production access, so no production token: an admin gives it"

# Nothing of THIS org answers to that fingerprint that this key may stop. The same 404 whether the
# row belongs to another org, was already revoked, or is somebody else's person key: a tenant
# learns nothing about the table.
NO_SUCH_KEY = "no live key of this org has the fingerprint {fingerprint}"


class WantedToken(WireModel):
    """What a server's token is for, and the one world it opens."""

    label: str
    env: str


@router.get("/v1/keys")
async def listed(key: KeyDep, keys: KeysDep, members: MembersDep) -> list[dict[str, Any]]:
    """The tokens this key may see, oldest first: every server's, and a person's own — every
    person's for a key that opens `keys`. Revoked ones are named as revoked."""
    names = {member.id: member.name for member in await members.listed(key.org)}
    return [
        _as_json(row, names)
        for row in await keys.listed(key.org)
        if row.subject is None or row.subject == key.subject or "keys" in key.scopes
    ]


# The one response of the tenant API that carries a key in the clear. It is read once by whoever
# asked and stored by nobody: the table keeps the sha256 and no door reads one back.
@router.post("/v1/keys")
async def issue(
    said: WantedToken, key: AppKeyDep, keys: KeysDep, members: MembersDep
) -> dict[str, Any]:
    """A server's token for this org, in the world named, answered once."""
    try:
        env = an_env(said.env)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    if not a_person(key):
        raise HTTPException(403, BY_A_PERSON)
    if is_a_deployment(env) and not await opens_production(key, members):
        raise HTTPException(403, NOT_IN_PRODUCTION.format(name=key.name or "this person"))
    issued = await keys.issue(
        org=key.org, label=said.label, env=env, scopes=SERVER_SCOPES, created_by=author_of(key)
    )
    return issued.as_json


# A POST and not a DELETE, because nothing is deleted: the row stays and grows a timestamp, so the
# log entries that name this key stay readable. A person stops their own keys and the server
# tokens they made; a key that opens `keys` stops any of the org's.
@router.post("/v1/keys/{fingerprint}/revoke")
async def revoke(fingerprint: str, key: KeyDep, keys: KeysDep) -> dict[str, Any]:
    """Stop honouring one key of this org from the next request. Its row, and its history, stay."""
    row = next((row for row in await keys.listed(key.org) if row.fingerprint == fingerprint), None)
    if row is None or row.revoked_at is not None or not _may_stop(key, row):
        raise HTTPException(404, NO_SUCH_KEY.format(fingerprint=fingerprint))
    if not await keys.revoke(fingerprint):
        raise HTTPException(404, NO_SUCH_KEY.format(fingerprint=fingerprint))
    return {"fingerprint": fingerprint, "revoked": True}


def _may_stop(key: KeyRecord, row: ListedKey) -> bool:
    """Whether this key may revoke that row: its own, one it made, or any with `keys`."""
    mine = key.subject is not None and key.subject in (row.subject, row.created_by)
    return mine or "keys" in key.scopes


def _as_json(row: ListedKey, names: dict[str, str]) -> dict[str, Any]:
    """One token as the Tokens screen draws it: whose, which world, who made it, when used."""
    person = row.subject is not None
    return {
        "fingerprint": row.fingerprint,
        "label": row.label,
        "kind": "person" if person else "server",
        # A person's key opens whatever their row does; only a server's token has a world.
        "env": None if person else row.env,
        "name": row.name,
        "created_by": None if row.created_by is None else names.get(row.created_by, row.created_by),
        "created_at": row.created_at,
        "last_used_at": row.last_used_at,
        "revoked_at": row.revoked_at,
        "scopes": list(row.scopes),
    }

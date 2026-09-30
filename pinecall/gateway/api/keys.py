"""The org's keys: listed by fingerprint, a server's token minted, one revoked."""

from datetime import UTC, datetime

from fastapi import APIRouter

from pinecall.domain.errors import NotAllowed, NotFound
from pinecall.domain.names import PRODUCTION, parse_env
from pinecall.domain.person import THE_KEYS
from pinecall.gateway._deps import AppKey, BearerDep, GatewayDep
from pinecall.tenancy import keys, people
from pinecall.tenancy.keys import Bearer, ListedKey
from pinecall.wire.rest.accounts import (
    CreateKeyRequest,
    KeyIssuedResponse,
    KeyRow,
    RevokeKeyResponse,
)

router = APIRouter()


# Made by a person and owned by the org, so it outlives whoever made it.
BY_A_PERSON = "a server's token is made by a person, from Tokens in the console"


NO_PRODUCTION = "{name} has no production access: a production token is made by somebody who has"


# One sentence for another org's key, a revoked one and one that is not yours to stop.
NO_SUCH_KEY = "no live key of this org has the fingerprint {fingerprint}"


# Any key: every server's token, and the asker's own person keys, or every person's with `keys`.
@router.get("/v1/keys")
async def list_keys(key: BearerDep, gateway: GatewayDep) -> list[KeyRow]:
    """The org's keys this key may see, oldest first, the revoked ones too; never a key."""
    pool = gateway.connections.pool
    names = {member.id: member.name for member in await people.listed(pool, key.key.org)}
    return [
        key_row(row, names)
        for row in await keys.listed(pool, key.key.org)
        if row.key.subject is None
        or row.key.subject == key.key.subject
        or THE_KEYS in key.key.scopes
    ]


# Only the fingerprint is kept: the token is in this answer and never again.
@router.post("/v1/keys")
async def create_key(body: CreateKeyRequest, key: AppKey, gateway: GatewayDep) -> KeyIssuedResponse:
    """A server's token for the org in one world, `pc_live_` or `pc_test_`."""
    env = parse_env(body.env)
    person = key.bearer.member
    if key.bearer.key.subject is None or person is None:
        raise NotAllowed(BY_A_PERSON)
    if env == PRODUCTION and not person.opens_production:
        raise NotAllowed(NO_PRODUCTION.format(name=person.name))
    keys.check_expiry(body.expires_at, datetime.now(UTC))
    issued = keys.Issued(
        org=key.org,
        env=env,
        scopes=keys.server_scopes(body.scopes),
        label=body.label,
        created_by=person.id,
        expires_at=body.expires_at,
    )
    minted, secret = await keys.issue(gateway.connections.pool, issued)
    return KeyIssuedResponse.of(minted, secret)


# A POST, not a DELETE: the row stays, so the entries that name the key still read.
@router.post("/v1/keys/{fingerprint}/revoke")
async def revoke_key(fingerprint: str, key: BearerDep, gateway: GatewayDep) -> RevokeKeyResponse:
    """Stop one of the org's keys from the next request on: your own, one you made, or any."""
    pool = gateway.connections.pool
    listed = await keys.listed(pool, key.key.org)
    row = next((row for row in listed if row.fingerprint == fingerprint), None)
    if row is None or row.revoked_at is not None or not _may_stop(key, row):
        raise NotFound(NO_SUCH_KEY.format(fingerprint=fingerprint))
    if not await keys.revoke(pool, fingerprint):
        raise NotFound(NO_SUCH_KEY.format(fingerprint=fingerprint))
    return RevokeKeyResponse(fingerprint=fingerprint, revoked=True)


def key_row(row: ListedKey, names: dict[str, str]) -> KeyRow:
    """A listed key as the doors send it, its maker named where the org still knows them."""
    is_a_persons = row.key.subject is not None
    return KeyRow(
        fingerprint=row.fingerprint,
        label=row.key.label,
        kind="person" if is_a_persons else "server",
        env=None if is_a_persons else row.key.env,
        name=row.key.name,
        created_by=None if row.created_by is None else names.get(row.created_by, row.created_by),
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
        scopes=sorted(row.key.scopes),
        expires_at=row.key.expires_at,
    )


def _may_stop(key: Bearer, row: ListedKey) -> bool:
    subject = key.key.subject
    mine = subject is not None and subject in (row.key.subject, row.created_by)
    return mine or THE_KEYS in key.key.scopes

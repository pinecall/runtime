"""The operator's doors onto the tenants: orgs, each one's keys, and the quotas set on each."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import TypeAdapter

from pinecall.api._deps import KeysDep, OrgsDep, RoutesDep, an_operator, an_org
from pinecall.auth.keys import Issued, ListedKey
from pinecall.types import DeclarationRefused, Org, Quotas, a_slug
from pinecall_protocol import WireModel

# Every /v1/ops door takes the operator key and nothing else, checked before the endpoint runs.
# The same gate the routes doors take, from api/_deps.py: there is one, and this is it.
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])

ORGS: TypeAdapter[tuple[Org, ...]] = TypeAdapter(tuple[Org, ...])
LISTED: TypeAdapter[tuple[ListedKey, ...]] = TypeAdapter(tuple[ListedKey, ...])
QUOTAS: TypeAdapter[Quotas] = TypeAdapter(Quotas)

# A removed org has nothing to say back.
NO_BODY = 204

# The slug is the operator's word for the tenant and two tenants cannot share one.
SLUG_TAKEN = "an org already answers to the slug {slug}"

# An org that still holds a live key or a route is an org somebody is still using: the keys are
# revoked and the numbers moved first, in the operator's own terminal, and only then does the
# row go. Nothing cascades into a tenant's doors.
STILL_IN_USE = "org {org} still has {what}: revoke its keys and remove its routes first"

# Nothing answers to that fingerprint, or it was already revoked. 404 and not 200: `keys revoke`
# on a typo must never read as done.
NO_SUCH_KEY = "no live key with fingerprint {fingerprint}"


class WantedOrg(WireModel):
    """What `orgs add` sends: the slug people will type, and a name when it differs."""

    slug: str
    name: str | None = None


class WantedKey(WireModel):
    """What `keys issue` sends: what the key is for. Whose it is, the path already said."""

    label: str | None = None


class WantedQuotas(WireModel):
    """What `orgs quota` sends: the whole set. A limit left out is no limit."""

    minutes: int | None = None
    messages: int | None = None
    agents: int | None = None
    concurrent_calls: int | None = None


# ── the orgs ────────────────────────────────────────────────────────────────────


@operator.get("/orgs")
async def listed(orgs: OrgsDep) -> list[dict[str, Any]]:
    """Every org, oldest first: the default one is always the first line."""
    return list(ORGS.dump_python(await orgs.listed(), mode="json"))


@operator.post("/orgs")
async def add(said: WantedOrg, orgs: OrgsDep) -> dict[str, Any]:
    """A new tenant. The id is minted here and is what every row of theirs will name."""
    try:
        slug = a_slug(said.slug)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    org = await orgs.create(slug, said.name or slug)
    if org is None:
        raise HTTPException(409, SLUG_TAKEN.format(slug=slug))
    return _as_json(org)


@operator.get("/orgs/{named}")
async def one(named: str, orgs: OrgsDep) -> dict[str, Any]:
    """One org, by its id or its slug, with the quotas set on it."""
    org = await an_org(named, orgs)
    return {**_as_json(org), "quotas": QUOTAS.dump_python(await orgs.quotas_of(org.id))}


@operator.delete("/orgs/{named}", status_code=NO_BODY)
async def remove(named: str, orgs: OrgsDep, keys: KeysDep, table: RoutesDep) -> None:
    """Forget the org. Refused while a live key or a route still names it."""
    org = await an_org(named, orgs)
    if any(key.revoked_at is None for key in await keys.listed(org.id)):
        raise HTTPException(409, STILL_IN_USE.format(org=org.slug, what="live keys"))
    if await table.of_org(org.id):
        raise HTTPException(409, STILL_IN_USE.format(org=org.slug, what="routes"))
    await orgs.remove(org.id)


# ── its quotas ──────────────────────────────────────────────────────────────────


@operator.put("/orgs/{named}/quotas")
async def set_quotas(named: str, said: WantedQuotas, orgs: OrgsDep) -> dict[str, Any]:
    """Replace the org's limits, whole. They bite the next call and the next register."""
    org = await an_org(named, orgs)
    try:
        quotas = Quotas(
            minutes=said.minutes,
            messages=said.messages,
            agents=said.agents,
            concurrent_calls=said.concurrent_calls,
        )
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    await orgs.set_quotas(org.id, quotas)
    dumped: dict[str, Any] = QUOTAS.dump_python(quotas)
    return dumped


# ── its keys ────────────────────────────────────────────────────────────────────


# The ONE response in the whole runtime that carries a key in the clear. It is built here, read
# once by the terminal that asked for it, and stored by nobody: docs/decisions/keys.md.
@operator.post("/orgs/{named}/keys")
async def issue(named: str, said: WantedKey, orgs: OrgsDep, keys: KeysDep) -> dict[str, Any]:
    """Mint a key for the org and answer with it, the once. The table keeps its sha256."""
    org = await an_org(named, orgs)
    return _issued_as_json(await keys.issue(org=org.id, label=said.label))


@operator.get("/orgs/{named}/keys")
async def keys_listed(named: str, orgs: OrgsDep, keys: KeysDep) -> list[dict[str, Any]]:
    """Every key of the org, oldest first, revoked ones included and named as revoked."""
    org = await an_org(named, orgs)
    return list(LISTED.dump_python(await keys.listed(org.id), mode="json"))


# A POST and not a DELETE, because nothing is deleted: the row stays and grows a timestamp, so the
# log entries that name this key stay readable. The verb on the wire says which of the two it is.
# It names no org: a fingerprint already names one row in the table.
@operator.post("/keys/{fingerprint}/revoke")
async def revoke(fingerprint: str, keys: KeysDep) -> dict[str, Any]:
    """Stop honouring one key from the next request. Its row, and its history, stay."""
    if not await keys.revoke(fingerprint):
        raise HTTPException(404, NO_SUCH_KEY.format(fingerprint=fingerprint))
    return {"fingerprint": fingerprint, "revoked": True}


def _as_json(org: Org) -> dict[str, Any]:
    """One org as the wire says it, through the adapter the listing already uses."""
    return dict(ORGS.dump_python((org,), mode="json")[0])


def _issued_as_json(issued: Issued) -> dict[str, Any]:
    """The issued key as the CLI reads it back: the key, and the record it was written under."""
    record = issued.record
    return {"key": issued.key, "key_id": record.key_id, "org": record.org, "label": record.label}

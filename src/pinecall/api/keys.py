"""/v1/keys: the org's own API keys — the one its server runs on — issued, listed, revoked."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import TypeAdapter

from pinecall.api._deps import ApiKeysKeyDep, KeysDep, MembersDep
from pinecall.auth.keys import KeyRecord, ListedKey
from pinecall.auth.members import Members
from pinecall.types import HOLDING, PRODUCTION, DeclarationRefused, an_env, key_scopes
from pinecall_protocol import WireModel

# The tenant's own three doors, on the org's API key, exactly as every other tenant door. They
# name no org: an API key IS its org, and a door that took the name would be a door that could
# mint a key into somebody else's. The operator's own are /v1/ops/orgs/{org}/keys (api/orgs.py).
router = APIRouter()

LISTED: TypeAdapter[tuple[ListedKey, ...]] = TypeAdapter(tuple[ListedKey, ...])

# A key may not hand out what its HOLDER does not hold, or the smallest role in an org would be a
# way to mint the largest. The sentence names what is missing, so the person reading it knows
# which of their own rights ran out rather than guessing at the whole set.
NOT_YOURS_TO_GIVE = "this key cannot issue {missing}: {whose} does not open {missing}"

# What a key is measured against, and it is NOT the key's own scopes when a person holds it.
#
# A person's key in production does not carry `app`: holding an agent there is a deployment, and a
# deployment is a machine (types/key.py). Measuring against the key made that absence contagious —
# an admin could not mint the key their own server runs on, in EITHER world, and the only key in
# the building that could was the box operator's. A tenant could not deploy at all.
#
# The right bound is the person's ROLE: what their org trusts them with. An admin's role opens
# `app`; what they may not do is hold an agent themselves in production, which is a rule about
# their own key and not about the machines they are trusted to set up. A key that names nobody is
# a machine's, and a machine is bounded by what it itself holds, exactly as before.
A_PERSON = "your role"
THIS_KEY = "this key"

# Nothing of THIS org answers to that fingerprint. The same 404 whether the row belongs to
# another org, was already revoked, or never existed: a tenant learns nothing about the table.
NO_SUCH_KEY = "no live key of this org has the fingerprint {fingerprint}"


class WantedKey(WireModel):
    """What the org asks for: what the key is for, which world it opens, what it may do there."""

    label: str | None = None
    # Production unless asked otherwise: a key issued here is a machine's, and a machine is a
    # deployment. A sandbox one is for CI, and is the deliberate act of saying so.
    env: str = PRODUCTION
    # Holding an agent, and nothing else, when nobody said: that is what a server does, and it is
    # the one thing a person's key may no longer do in production (types/key.py).
    scopes: list[str] | None = None


@router.get("/v1/keys")
async def listed(key: ApiKeysKeyDep, keys: KeysDep) -> list[dict[str, Any]]:
    """Every key of this org by its fingerprint, oldest first, revoked ones named as revoked."""
    return list(LISTED.dump_python(await keys.listed(key.org), mode="json"))


# The one response of the tenant API that carries a key in the clear. It is read once by whoever
# asked and stored by nobody: the table keeps the sha256 and no door reads one back.
@router.post("/v1/keys")
async def issue(
    said: WantedKey, key: ApiKeysKeyDep, keys: KeysDep, members: MembersDep
) -> dict[str, Any]:
    """A key for a machine of this org, answered once. It names nobody: people log in."""
    try:
        env = an_env(said.env)
        wanted = frozenset({HOLDING}) if said.scopes is None else key_scopes(said.scopes)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    allowed, whose = await _what_the_asker_may_give(key, members)
    if missing := wanted - allowed:
        said_missing = " · ".join(sorted(missing))
        raise HTTPException(403, NOT_YOURS_TO_GIVE.format(missing=said_missing, whose=whose))
    issued = await keys.issue(org=key.org, label=said.label, env=env, scopes=wanted)
    return issued.as_json


async def _what_the_asker_may_give(key: KeyRecord, members: Members) -> tuple[frozenset[str], str]:
    """The bound on what this key may mint, and the word the refusal calls it by. See A_PERSON."""
    if key.subject is None:
        return key.scopes, THIS_KEY
    member = await members.find(key.org, key.subject)
    # A key naming somebody the table no longer has an active row for is bounded by itself: they
    # were removed or disabled, and a removed person's key must not keep their role's reach.
    if member is None or member.status != "active":
        return key.scopes, THIS_KEY
    return member.scopes, A_PERSON


# A POST and not a DELETE, because nothing is deleted: the row stays and grows a timestamp, so the
# log entries that name this key stay readable.
@router.post("/v1/keys/{fingerprint}/revoke")
async def revoke(fingerprint: str, key: ApiKeysKeyDep, keys: KeysDep) -> dict[str, Any]:
    """Stop honouring one key of this org from the next request. Its row, and its history, stay."""
    if not await _is_the_orgs(fingerprint, key, keys):
        raise HTTPException(404, NO_SUCH_KEY.format(fingerprint=fingerprint))
    if not await keys.revoke(fingerprint):
        raise HTTPException(404, NO_SUCH_KEY.format(fingerprint=fingerprint))
    return {"fingerprint": fingerprint, "revoked": True}


# The fingerprint names one row of the whole table, so the org is checked before the UPDATE and
# not after it: a tenant may revoke its own keys and must not be able to touch — or probe for —
# anybody else's.
async def _is_the_orgs(fingerprint: str, key: KeyRecord, keys: KeysDep) -> bool:
    """Whether a live key of this org hashes to that fingerprint."""
    return any(
        row.fingerprint == fingerprint and row.revoked_at is None
        for row in await keys.listed(key.org)
    )

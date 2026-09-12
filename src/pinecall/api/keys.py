"""/v1/keys: the org's own API keys — the one its server runs on — issued, listed, revoked."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import TypeAdapter

from pinecall.api._deps import ApiKeysKeyDep, KeysDep
from pinecall.auth.keys import KeyRecord, ListedKey
from pinecall.types import HOLDING, PRODUCTION, DeclarationRefused, an_env, key_scopes
from pinecall_protocol import WireModel

# The tenant's own three doors, on the org's API key, exactly as every other tenant door. They
# name no org: an API key IS its org, and a door that took the name would be a door that could
# mint a key into somebody else's. The operator's own are /v1/ops/orgs/{org}/keys (api/orgs.py).
router = APIRouter()

LISTED: TypeAdapter[tuple[ListedKey, ...]] = TypeAdapter(tuple[ListedKey, ...])

# A key may not hand out what it does not itself hold, or the smallest role in an org would be a
# way to mint the largest. The sentence names what is missing, so the person reading it knows
# which of their own scopes ran out rather than guessing at the whole set.
NOT_YOURS_TO_GIVE = "this key cannot issue {missing}: it does not open {missing} itself"

# Nothing of THIS org answers to that fingerprint. The same 404 whether the row belongs to
# another org, was already revoked, or never existed: a tenant learns nothing about the table.
NO_SUCH_KEY = "no live key of this org has the fingerprint {fingerprint}"


class WantedKey(WireModel):
    """What the org asks for: what the key is for, which world it opens, what it may do there."""

    label: str | None = None
    # Production unless asked otherwise: a key issued here is a machine's, and a machine is a
    # deployment. A development one is for CI, and is the deliberate act of saying so.
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
async def issue(said: WantedKey, key: ApiKeysKeyDep, keys: KeysDep) -> dict[str, Any]:
    """A key for a machine of this org, answered once. It names nobody: people log in."""
    try:
        env = an_env(said.env)
        wanted = frozenset({HOLDING}) if said.scopes is None else key_scopes(said.scopes)
    except DeclarationRefused as refused:
        raise HTTPException(400, str(refused)) from refused
    if missing := wanted - key.scopes:
        raise HTTPException(403, NOT_YOURS_TO_GIVE.format(missing=" · ".join(sorted(missing))))
    issued = await keys.issue(org=key.org, label=said.label, env=env, scopes=wanted)
    return issued.as_json


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

"""One org's own provider keys through two doors: the tenant's, on its key, and the operator's."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from pinecall.api._deps import OrgsDep, ProviderKeysKeyDep, UnlockedVaultDep, an_org
from pinecall.api._operator import an_operator
from pinecall.api.orgs import NO_BODY
from pinecall.types import VENDORS
from pinecall_protocol import WireModel

# The tenant's own three doors, on the org's API key, exactly as every other tenant door. They
# name no org: an API key IS its org, and a door that took the name would be a door that could
# write into somebody else's vault. A hosted tenant is BYOK-first and holds no operator key.
router = APIRouter()

# The same gate every /v1/ops door takes. The second one — a runtime that was given no vault key
# cannot keep a tenant's key at all — is UnlockedVaultDep, on each of the six endpoints.
operator = APIRouter(prefix="/v1/ops", dependencies=[Depends(an_operator)])

# 400 and not 422: the body was well formed and the word in the path is not one of ours. The list
# is in the sentence because an operator who typed `11labs` needs to know what to type instead.
NO_SUCH_VENDOR = "no vendor named {vendor}; this build runs: {known}"

# Nothing answers to that (org, vendor). 404 and not 204: `provider-key rm` on a typo must never
# read as done, the same rule `keys revoke` and `routes rm` already follow.
NO_SUCH_KEY = "org {org} has no {vendor} key"


class WantedKey(WireModel):
    """What either door takes, in one shape: the org's own key for one vendor, and nothing else."""

    key: str


@operator.put("/orgs/{named}/provider-keys/{vendor}", status_code=NO_BODY)
async def keep(
    named: str, vendor: str, said: WantedKey, orgs: OrgsDep, vault: UnlockedVaultDep
) -> None:
    """The org's own key for one vendor, from the next call on. Replaces whatever it had."""
    org = await an_org(named, orgs)
    await vault.put(org.id, _a_known_vendor(vendor), said.key)


@operator.delete("/orgs/{named}/provider-keys/{vendor}", status_code=NO_BODY)
async def forget(named: str, vendor: str, orgs: OrgsDep, vault: UnlockedVaultDep) -> None:
    """Back to the box's own key for that vendor, from the next call on."""
    org = await an_org(named, orgs)
    if not await vault.drop(org.id, _a_known_vendor(vendor)):
        raise HTTPException(404, NO_SUCH_KEY.format(org=org.slug, vendor=vendor))


# Names, and never a value — not even a prefix. What is worth knowing about a stored key is
# whether it is there; anything more is a way to read a secret back, and there is none.
@operator.get("/orgs/{named}/provider-keys")
async def vendors(named: str, orgs: OrgsDep, vault: UnlockedVaultDep) -> dict[str, list[str]]:
    """Which vendors this org brought its own key for. The rest run on the box's."""
    org = await an_org(named, orgs)
    return {"vendors": list(await vault.vendors_of(org.id))}


# ── the tenant's own, on its own key ────────────────────────────────────────────


@router.put("/v1/provider-keys/{vendor}", status_code=NO_BODY)
async def bring(
    vendor: str, said: WantedKey, key: ProviderKeysKeyDep, vault: UnlockedVaultDep
) -> None:
    """This org's own key for one vendor, from the next call on. Replaces whatever it had."""
    await vault.put(key.org, _a_known_vendor(vendor), said.key)


@router.delete("/v1/provider-keys/{vendor}", status_code=NO_BODY)
async def take_back(vendor: str, key: ProviderKeysKeyDep, vault: UnlockedVaultDep) -> None:
    """Back to the box's own key for that vendor, from the next call on."""
    if not await vault.drop(key.org, _a_known_vendor(vendor)):
        raise HTTPException(404, NO_SUCH_KEY.format(org=key.org, vendor=vendor))


# The tenant reads the same names the operator reads and the same nothing else: a key goes INTO
# this door and never comes back out of it. The only body in the runtime that carries one is the
# worker's own, api/agents/provider_keys.py, and that is what makes this feature auditable.
@router.get("/v1/provider-keys")
async def brought(key: ProviderKeysKeyDep, vault: UnlockedVaultDep) -> dict[str, list[str]]:
    """Which vendors this org brought its own key for. The rest run on the box's."""
    return {"vendors": list(await vault.vendors_of(key.org))}


def _a_known_vendor(vendor: str) -> str:
    """The vendor as this build spells it, or 400 with every name it does have."""
    if vendor not in VENDORS:
        known = ", ".join(VENDORS)
        raise HTTPException(400, NO_SUCH_VENDOR.format(vendor=vendor, known=known))
    return vendor

"""The one door in the runtime that answers with a provider key: the worker asking for its org's."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinecall.api._corner import CornerDep
from pinecall.api._deps import AppKeyDep, OrgsDep, VaultDep
from pinecall.api.agents.registry import NO_AGENT, RegistryDep
from pinecall.orgs.vault import brought_by

router = APIRouter()


# The same check /v1/agents/{slug}/config makes, in the same words: an API key IS its org, and a
# slug another org holds is a 404 here — that this agent exists at all is not the asker's business.
# What comes back is the org's OWN keys, decrypted, and it goes to the worker holding that org's
# key — or to the box's worker, asking for the org of the call it is about to run on those keys —
# and to nobody else. Everything under docs/decisions/provider-keys.md hangs on this one door.
@router.get("/v1/agents/{slug}/provider-keys")
async def provider_keys(
    slug: str,
    key: AppKeyDep,  # noqa: ARG001 — the scope is asked here; the corner says where
    corner: CornerDep,
    registry: RegistryDep,
    vault: VaultDep,
    orgs: OrgsDep,
) -> dict[str, object]:
    """The keys this org brought of its own — empty is the common case: the box's env keys run —
    and which of the box's it is lent (`lends`: null lends every one)."""
    held = registry.of(corner.env, slug, corner.holder)
    if held is None or held.org != corner.org:
        raise HTTPException(status_code=404, detail=NO_AGENT.format(slug=slug))
    brought = await brought_by(vault, orgs.quotas_of, corner.org)
    lends = None if brought.lends is None else sorted(brought.lends)
    return {"keys": dict(brought.keys), "lends": lends}

"""The one door in the runtime that answers with a provider key: the worker asking for its org's."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinecall.api._corner import CornerDep
from pinecall.api._deps import AppKeyDep, VaultDep
from pinecall.api.agents.registry import NO_AGENT, RegistryDep
from pinecall.orgs.vault import keys_brought_by
from pinecall.types import ProviderKeys

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
) -> dict[str, ProviderKeys]:
    """The keys this org brought of its own. Empty is the common case: the box's env keys run."""
    held = registry.of(corner.env, slug, corner.holder)
    if held is None or held.org != corner.org:
        raise HTTPException(status_code=404, detail=NO_AGENT.format(slug=slug))
    return {"keys": dict(await keys_brought_by(vault, corner.org))}

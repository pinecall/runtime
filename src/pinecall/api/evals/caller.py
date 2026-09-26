"""POST /v1/evals/caller: the next thing a simulated caller says, improvised where the keys are."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from pinecall.api.deps import EvalsKeyDep, LlmsDep, OrgsDep, VaultDep
from pinecall.evals.simulated_caller import NO_MODEL, Asking, Improvised, what_they_say_next
from pinecall.orgs.vault import brought_by
from pinecall.providers.models import NoProvider
from pinecall.providers.tuned_declaration import tuned_llm
from pinecall.types import DeclarationRefused

router = APIRouter()

THE_MODEL_REFUSED = "the model playing the caller answered nothing usable: {broke}"


# The model runs HERE because this is the process holding the provider keys — a tenant's terminal
# has the persona and the transcript, and this door has the only thing it is missing. Which is why
# the key is an identity here and not only a gate: the caller is played on the org's own key when
# it brought one, so a tenant who simulates a hundred turns spends their account and not the box's.
# WHICH model is the persona's own `llm`, in the agent's own words; none is the box's default.
@router.post("/v1/evals/caller")
async def next_line(
    said: Asking, key: EvalsKeyDep, llms: LlmsDep, vault: VaultDep, orgs: OrgsDep
) -> Improvised:
    """One turn of an improvised caller: the persona and the call so far in, one line out."""
    try:
        llm = llms(tuned_llm(said.persona.llm), await brought_by(vault, orgs.quotas_of, key.org))
    except DeclarationRefused as refused:
        raise HTTPException(422, str(refused)) from refused
    except NoProvider as missing:
        raise HTTPException(503, NO_MODEL.format(missing=missing)) from missing
    try:
        return await what_they_say_next(llm, said)
    except ValueError as broke:
        raise HTTPException(502, THE_MODEL_REFUSED.format(broke=broke)) from broke

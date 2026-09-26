"""POST /v1/agents/{slug}/memory/extraction: the write side of memory, held to its own goldens."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException

from pinecall.api.agents.held_agent import Registration
from pinecall.api.agents.session_config import tuned_for
from pinecall.api.deps import LlmsDep, MemoryKeyDep, OrgsDep, TuningDep, VaultDep
from pinecall.api.scope.request_scope import HeldDep
from pinecall.auth.keys import KeyRecord, is_held_by
from pinecall.memory.extraction import ask_model
from pinecall.memory.goldens import facts_of, judge_extraction, turns_of, undeclared_category
from pinecall.orgs.vault import brought_by
from pinecall.providers.models import DEFAULT_VENDOR, Chat
from pinecall.types import AgentConfig, MemoryPolicy, Model
from pinecall_protocol.rest import (
    ExtractionCases,
    ExtractionGolden,
    ExtractionJudged,
    ExtractionRun,
)

router = APIRouter()

# A class that keeps nothing pays for no model call at hang-up either, so there is nothing here to
# hold to a golden. 400 and not an empty pass: a suite that answers green about a feature the class
# does not have is worse than no suite.
KEEPS_NOTHING = "agent {slug} declares no memory.remember: there is nothing to extract"


# The hang-up's one model call, run over a call that already happened, and judged by code. It is a
# door and not a CLI-side loop for the reason `pinecall test` scores in the gateway: the org's
# model, the org's provider keys and the class's resolved declaration are all here and none of
# them is the tenant process's. What comes back is what a person reads and a pipeline exits on.
@router.post("/v1/agents/{slug}/memory/extraction")
async def extraction(
    slug: str,
    said: ExtractionCases,
    key: MemoryKeyDep,
    held: HeldDep,
    kept: TuningDep,
    llms: LlmsDep,
    vault: VaultDep,
    orgs: OrgsDep,
) -> ExtractionRun:
    """Every case through one extraction each, and the four questions asked of what came back."""
    config = await _the_agent(slug, held, key, kept)
    policy = config.memory
    if policy is None or not policy.remember:
        raise HTTPException(status_code=400, detail=KEEPS_NOTHING.format(slug=slug))
    for case in said.cases:
        if (wrong := undeclared_category(case, policy)) is not None:
            raise HTTPException(status_code=400, detail=wrong)
    started = time.perf_counter()
    chat = llms(config.llm, await brought_by(vault, orgs.quotas_of, key.org))
    try:
        # One at a time, as the knowledge golden asks its questions: a suite is run when somebody
        # changed the prompt or the model, never on a caller's clock, and a dozen extractions at
        # once would measure the vendor's concurrency and not the extraction.
        results = [await _one(case, chat, config) for case in said.cases]
    finally:
        await chat.aclose()
    return _as_an_answer(slug, config.llm, results, (time.perf_counter() - started) * 1000)


async def _one(case: ExtractionGolden, chat: Chat, config: AgentConfig) -> ExtractionJudged:
    """One case: what the model asked for, then the policy and the four checks over its answer."""
    policy = config.memory or MemoryPolicy()
    known = facts_of(case)
    said = await ask_model(
        chat, known=known, turns=turns_of(case), policy=policy, channel=case.channel
    )
    return judge_extraction(case, said, policy=policy, known=known, tools=config.tools)


async def _the_agent(slug: str, held: Registration, key: KeyRecord, kept: TuningDep) -> AgentConfig:
    """The declaration this run is judged against, with the org's tuning already laid over it."""
    resolved = await tuned_for(kept, key.org, key.env, is_held_by(key), slug, held.config)
    return resolved.config


def _as_an_answer(
    slug: str, llm: Model | None, results: list[ExtractionJudged], took_ms: float
) -> ExtractionRun:
    """The run as the verb prints it: which model answered, how many held, and every case."""
    return ExtractionRun(
        agent=slug,
        model=f"{llm.provider}/{llm.model}" if llm else DEFAULT_VENDOR,
        cases=len(results),
        held=sum(1 for one in results if one.held),
        took_ms=took_ms,
        results=results,
    )

"""POST /v1/evals/replay/{call}: a finished call read back and judged by code, never by a model."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import Field

from pinecall.api._deps import EvalsKeyDep, StoreDep
from pinecall.api.agents.registry import RegistryDep
from pinecall.api.calls.sink import NOT_YOUR_ORGS, declared_by
from pinecall.evals.checks import replayed as replay
from pinecall.evals.checks.consent import consent
from pinecall.evals.checks.errors import errors
from pinecall.evals.checks.latency import DEFAULT_BUDGET, latency
from pinecall.evals.checks.register import register
from pinecall.log.replay import whole
from pinecall.types import AgentConfig
from pinecall_protocol import WireModel

router = APIRouter()

# Nothing was ever written under this id. The same 404 the state door answers, for the same reason:
# a call this gateway's log never heard of cannot be re-evaluated, and a typo must not read as a
# call that passed every check.
NO_SUCH_CALL = "no log for call {call}"


class Case(WireModel):
    """What the caller declares about this call: its words, and the latencies it is held to."""

    banned: list[str] = Field(default_factory=list[str])
    budget: dict[str, float] = Field(default_factory=dict[str, float])


# The key says whose log may be replayed and nothing more: the verdict does not depend on it,
# but a call is one org's, and another org's key is refused in the sink's own words.
@router.post("/v1/evals/replay/{call}")
async def replay_call(
    call: str,
    key: EvalsKeyDep,
    store: StoreDep,
    registry: RegistryDep,
    said: Case | None = None,
) -> dict[str, Any]:
    """Rebuild the call from its log and answer the four code checks over it, in one round trip."""
    owner = await store.owner(call, "")
    if owner is not None and owner != key.org:
        raise HTTPException(403, NOT_YOUR_ORGS)
    entries = await whole(store, call)
    if not entries:
        raise HTTPException(404, NO_SUCH_CALL.format(call=call))
    rebuilt = replay.rebuild(entries)
    case = said or Case()
    declared = declared_by(registry, rebuilt.agent)
    verdicts = [
        consent(rebuilt, _irreversible(declared)),
        register(rebuilt, case.banned),
        errors(rebuilt),
        latency(rebuilt, case.budget or DEFAULT_BUDGET),
    ]
    return {
        "call": rebuilt.call or call,
        "agent": rebuilt.agent,
        "passed": not any(verdict.status == "failed" for verdict in verdicts),
        "verdicts": [verdict.as_json for verdict in verdicts],
    }


def _irreversible(declared: AgentConfig | None) -> frozenset[str] | None:
    """Which tools change the world for good. None when no app declares this agent right now."""
    if declared is None:
        return None
    return frozenset(tool.name for tool in declared.tools if tool.side_effect == "irreversible")

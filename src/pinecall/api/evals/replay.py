"""POST /v1/evals/replay/{call}: a finished call read back and judged by code, never by a model."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import Field

from pinecall.api.agents.registry import RegistryDep
from pinecall.api.calls.log_sink import NO_SUCH_CALL, declared_by, require_calls_scope
from pinecall.api.deps import CallIndexDep, EvalsKeyDep, StoreDep
from pinecall.evals.checks import replay
from pinecall.evals.checks.check_verdict import Verdict
from pinecall.evals.checks.consent import consent
from pinecall.evals.checks.latency import DEFAULT_BUDGET, latency
from pinecall.evals.checks.provider_errors import errors
from pinecall.evals.checks.register import register
from pinecall.log.replay import whole
from pinecall.types import AgentConfig
from pinecall_protocol import WireModel
from pinecall_protocol.defs import ScoreVerdict

router = APIRouter()


class Case(WireModel):
    """What the caller declares about this call: its words, and the latencies it is held to."""

    banned: list[str] = Field(default_factory=list[str])
    budget: dict[str, float] = Field(default_factory=dict[str, float])


class VerdictSaid(WireModel):
    """One check as the door answers it: its name, the word a script branches on, and why."""

    check: str
    status: ScoreVerdict
    detail: str


class Replayed(WireModel):
    """POST /v1/evals/replay/{call}: the call, its agent, whether every check held, and each."""

    call: str
    agent: str
    passed: bool
    verdicts: list[VerdictSaid]


# The key says whose log may be replayed and nothing more: the verdict does not depend on it. The
# call is asked for in its corner — org, world, holder — exactly as the judge door beside this one
# asks, and a call outside it is the same 404 as a typo: a typo must never read as a call that
# passed every check, and another tenant's call must never read as anything.
@router.post("/v1/evals/replay/{call}")
async def replay_call(
    call: str,
    key: EvalsKeyDep,
    store: StoreDep,
    index: CallIndexDep,
    registry: RegistryDep,
    said: Case | None = None,
) -> Replayed:
    """Rebuild the call from its log and answer the four code checks over it, in one round trip."""
    await require_calls_scope(index, key, call)
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
    return Replayed(
        call=rebuilt.call or call,
        agent=rebuilt.agent,
        passed=not any(verdict.status == "broken" for verdict in verdicts),
        verdicts=[_said(verdict) for verdict in verdicts],
    )


def _said(verdict: Verdict) -> VerdictSaid:
    """The verdict as the door answers it: three strings, no nesting, nothing to decode."""
    return VerdictSaid(check=verdict.check, status=verdict.status, detail=verdict.detail)


def _irreversible(declared: AgentConfig | None) -> frozenset[str] | None:
    """Which tools change the world for good. None when no app declares this agent right now."""
    if declared is None:
        return None
    return frozenset(tool.name for tool in declared.tools if tool.side_effect == "irreversible")

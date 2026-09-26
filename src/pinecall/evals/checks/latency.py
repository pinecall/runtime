"""The latency verdict: the medians a call was answered at, against the budget it is held to."""

from __future__ import annotations

from collections.abc import Mapping
from statistics import median

from pinecall.evals.checks.check_verdict import Verdict, broken, held, skipped
from pinecall.evals.checks.replay import Replayed

CHECK = "latency"

# What a call is held to when nobody said otherwise, in seconds, under livekit's own field names.
# A voice turn stops feeling like a conversation past two seconds end to end; the model's first
# token and the voice's first byte are the two halves a person can act on when it does. An agent
# that wants its own numbers sends them with the request — AgentConfig carries no budget today.
DEFAULT_BUDGET: Mapping[str, float] = {
    "e2e_latency": 2.0,
    "llm_node_ttft": 1.0,
    "tts_node_ttfb": 0.6,
}

NOTHING_MEASURED = "no turn of this call carried any of {names}"


def latency(call: Replayed, budget: Mapping[str, float] = DEFAULT_BUDGET) -> Verdict:
    """The median of each judged latency against its budget. Medians, never a single turn."""
    measured = {
        name: median(values) for name, values in call.latencies.items() if name in budget and values
    }
    if not measured:
        return skipped(CHECK, NOTHING_MEASURED.format(names=", ".join(budget)))
    over = [name for name, seconds in measured.items() if seconds > budget[name]]
    said = "; ".join(_said(name, seconds, budget[name], call) for name, seconds in measured.items())
    return broken(CHECK, said) if over else held(CHECK, said)


def _said(name: str, seconds: float, allowed: float, call: Replayed) -> str:
    """One measure as a person reads it: the median, the budget, and how many turns carried it."""
    sign = ">" if seconds > allowed else "<="
    return f"{name} {seconds:.3f}s {sign} {allowed:.3f}s over {len(call.latencies[name])} turns"

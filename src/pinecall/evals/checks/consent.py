"""The consent check: an irreversible tool runs only after the caller was asked and said yes."""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import replace

from pinecall.evals.checks.check_verdict import Verdict, broken, deferred, held, skipped
from pinecall.evals.checks.replay import Replayed
from pinecall.types import ConsentOutcome, GateLine, consent_of

CHECK = "consent"

NO_DECLARATION = (
    "agent {agent} is not registered on this gateway, so no tool's side effect is known: "
    "run this while the app that declares the tools is connected"
)

# The rule answers four words and a check answers four statuses, and this is the whole mapping
# between them. `ungated` is deferred and never failed: the gate itself was taken out of the
# runtime on purpose, and the sentence the rule returns carries that date.
AS_A_VERDICT: Mapping[ConsentOutcome, Callable[[str, str], Verdict]] = {
    "kept": held,
    "broken": broken,
    "ungated": deferred,
    "undeclared": skipped,
}


def consent(call: Replayed, irreversible: Collection[str] | None) -> Verdict:
    """Every irreversible tool call preceded by a granted confirmation for the same audience."""
    if irreversible is None:
        return skipped(CHECK, NO_DECLARATION.format(agent=call.agent))
    read = consent_of(_as_declared(call.gate, irreversible))
    return AS_A_VERDICT[read.outcome](CHECK, read.detail)


# The log says which tools ran, never what they do, so the registry's answer is written onto the
# trace here — which is the whole of this door's share of the rule. `types/consent.py`
# holds the rest, and the eval graph and ring 4 read the very same function.
def _as_declared(gate: Iterable[GateLine], irreversible: Collection[str]) -> list[GateLine]:
    """The gate's trace with every tool call marked irreversible or not, as the registry has it."""
    return [
        replace(line, side_effect="irreversible" if line.tool in irreversible else "read")
        if line.kind == "tool.call"
        else line
        for line in gate
    ]

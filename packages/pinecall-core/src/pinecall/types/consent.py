"""The order rule: an irreversible tool runs only after a granted confirmation of its own."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from pinecall.types.tool_spec import SideEffect

# The four entries the rule reads, under the log's own type names: the tool that ran, and the three
# halves of the gate around it. Every other entry of a call is a different question.
type GateKind = Literal["tool.call", "confirm.request", "confirm.granted", "confirm.declined"]

CONFIRMATIONS: frozenset[str] = frozenset(
    {"confirm.request", "confirm.granted", "confirm.declined"}
)

# Four words and not two, for the same reason a check answers four: the two cases nobody may read
# as a yes are a gate that is not there at all, and a call nothing could look at.
type ConsentOutcome = Literal["kept", "broken", "ungated", "undeclared"]

IRREVERSIBLE: SideEffect = "irreversible"

# The day the gate was taken out of the runtime, and the sentence a real call gets instead of a
# false FAIL: nothing mints a confirm.granted today, so an irreversible tool runs straight through
# and reads its template back. See docs/decisions/confirm.md.
GATE_DEFERRED_ON = "2026-09-06"
GATE_DEFERRED = (
    f"the confirmation gate is deferred ({GATE_DEFERRED_ON}, docs/decisions/confirm.md): "
    "{count} irreversible tool call(s) ran and the log carries no confirm.* at all"
)

NOTHING_DECLARED = (
    "{count} tool call(s) and not one declared side effect, "
    "so nothing here says which of them are irreversible"
)


@dataclass(frozen=True)
class GateLine:
    """One line of the gate's trace: a tool that ran, or a confirmation that was written."""

    seq: int
    kind: GateKind
    call_id: str
    tool: str
    audience: str | None = None
    # The log never says what a tool does — `tool.call` carries a name and arguments and nothing
    # else. Whoever builds the trace writes the app's declaration onto it; None means nobody could.
    side_effect: SideEffect | None = None


@dataclass(frozen=True)
class ConsentRead:
    """What the rule found: the word a caller branches on, and the sentence that proves it."""

    outcome: ConsentOutcome
    detail: str


def consent_of(gate: Iterable[GateLine]) -> ConsentRead:
    """Which irreversible tool call ran without a yes of its own, named by the seqs that say so."""
    lines = tuple(gate)
    calls = tuple(line for line in lines if line.kind == "tool.call")
    if calls and all(line.side_effect is None for line in calls):
        return ConsentRead("undeclared", NOTHING_DECLARED.format(count=len(calls)))
    ran = tuple(line for line in calls if line.side_effect == IRREVERSIBLE)
    if not ran:
        return ConsentRead(
            "kept", f"no irreversible tool ran in this call; {len(calls)} tool call(s) did"
        )
    confirmations = tuple(line for line in lines if line.kind in CONFIRMATIONS)
    if not confirmations:
        return ConsentRead("ungated", GATE_DEFERRED.format(count=len(ran)))
    faults = [
        fault
        for called in ran
        if (fault := _ungranted(called, _about(confirmations, called.call_id))) is not None
    ]
    if faults:
        return ConsentRead("broken", "; ".join(faults))
    return ConsentRead(
        "kept", f"{len(ran)} irreversible tool call(s), each after a granted confirmation"
    )


# A grant is minted for one tool call, so it is matched by call_id and never by name: two bookings
# in one conversation each have their own yes, and a grant that arrived late is the very bug this
# rule exists to catch. The seqs are the evidence, because a reader who disagrees with the verdict
# has to be able to open the log at the two lines it is about.
def _ungranted(called: GateLine, about: Sequence[GateLine]) -> str | None:
    """What is wrong with this tool call's confirmation, or None when nothing is."""
    granted = _first(about, "confirm.granted")
    if granted is None:
        return _no_yes_at_all(called, _first(about, "confirm.declined"))
    if granted.seq > called.seq:
        return (
            f"{called.tool} ran at seq {called.seq}, "
            f"before its confirm.granted at seq {granted.seq}"
        )
    asked = _first(about, "confirm.request")
    if asked is not None and asked.audience is not None and asked.audience != granted.audience:
        return (
            f"{called.tool} at seq {called.seq} was confirmed by {granted.audience} "
            f"and asked of {asked.audience} at seq {asked.seq}"
        )
    return None


# A caller who said no is a sharper finding than a caller who was never asked, and it is the same
# break: what the rule wants is a grant, and a decline is the log saying one was refused.
def _no_yes_at_all(called: GateLine, declined: GateLine | None) -> str:
    """The sentence for a tool that ran with no grant: after an explicit no, or after nothing."""
    if declined is not None:
        return (
            f"{called.tool} ran at seq {called.seq}, "
            f"after its confirm.declined at seq {declined.seq}"
        )
    return f"{called.tool} ran at seq {called.seq} with no confirm.granted before it"


def _about(confirmations: Sequence[GateLine], call_id: str) -> tuple[GateLine, ...]:
    """Every confirmation written about one tool call, in the order the log wrote them."""
    return tuple(line for line in confirmations if line.call_id == call_id)


def _first(about: Sequence[GateLine], kind: GateKind) -> GateLine | None:
    """The first confirmation of one kind about a tool call; a gate writes at most one of each."""
    return next((line for line in about if line.kind == kind), None)

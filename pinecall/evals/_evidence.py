"""What a call's agent could know, as a judge that reads the evidence is shown it."""

import json
from dataclasses import dataclass

from pinecall.domain.names import JsonObject
from pinecall.evals.case import Called, Case, calls_of


@dataclass(frozen=True)
class Evidence:
    """What the agent could know: the written text, its tool calls and answers, its states."""

    text: tuple[str, ...]
    # `name(arguments) → answer`: an empty answer means something only beside its arguments.
    calls: tuple[str, ...]
    state: tuple[str, ...] = ()


# A seeded state is rendered into the prompt with no tool running, so it is evidence too.
def evidence_of(case: Case) -> Evidence:
    """What the agent could know: text shown to it, tool calls with their answers, its states."""
    retrieved = [chunk for turn in case.turns for chunk in turn.retrieved]
    return Evidence(
        text=(*case.evidence, *retrieved),
        calls=tuple(
            tool_call_text(called) for called in calls_of(case) if called.answer is not None
        ),
        state=tuple(dict.fromkeys(_as_json(state) for state in case.states)),
    )


# A judge reads a bare `[]` as "no information": the name and the arguments say what it answers.
def tool_call_text(called: Called) -> str:
    """A tool call as a judge reads it: `name(arguments) → answer`."""
    return f"{called.name}({_as_json(called.arguments)}) → {called.answer}"


def as_text(evidence: Evidence) -> str:
    """Every piece of the evidence, one block apart, as a judge model reads it."""
    return "\n\n".join((*evidence.text, *evidence.calls, *evidence.state))


def _as_json(value: JsonObject) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)

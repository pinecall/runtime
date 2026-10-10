"""Pinecall's judges, written in evals/library/judges/, and the panel a finished call meets."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.judging import JudgeSpec
from pinecall.evals.case import Case

LIBRARY = Path(__file__).parent / "library" / "judges"


# What decides, before any model is asked, that a judge does not apply: an irreversible tool
# never ran, the call came in, the simulated caller expected nothing. Code reads facts here; it
# never answers the question.
type Gate = Literal["none", "irreversible-tools", "outbound", "expectations"]


NOT_A_PAGE = "{path}: a judge of the library opens with its front matter between two --- lines"
NOT_A_FIELD = "{path}: {field} is {value!r}, not one of {allowed}"


@dataclass(frozen=True)
class Written:
    """One of Pinecall's judges: its spec, what it holds a call to, its version and default."""

    spec: JudgeSpec
    summary: str
    version: int
    on_by_default: bool
    gate: Gate


@dataclass(frozen=True)
class Seated:
    """A judge on one call's panel: its spec, what may settle it as N/A first, and whose it is."""

    spec: JudgeSpec
    gate: Gate = "none"
    # The library's version, so a changed question reads as a changed judge; None for the org's own.
    version: int | None = None


@dataclass(frozen=True)
class Surroundings:
    """What the seal knows of a call beyond its log, as the judges that read facts are told it."""

    org: str
    # The sentence outbound calls open with; None when the org and the platform set none.
    disclosure: str | None = None
    # A do-not-call fact was written with this call's id (call.opt_out).
    opted_out: bool = False


GATES: tuple[Gate, ...] = ("none", "irreversible-tools", "outbound", "expectations")


# Read once a process: the files ship in the wheel and change only with a release.
@cache
def library() -> dict[str, Written]:
    """Every judge of the library, by name."""
    read = [_written(path) for path in sorted(LIBRARY.glob("*.md"))]
    return {judge.spec.name: judge for judge in read}


def panel_of(case: Case, switches: Mapping[str, bool], own: Sequence[JudgeSpec]) -> list[Seated]:
    """The library's judges switched on, then the org's and the agent's own, for this call."""
    seated = [
        Seated(judge.spec, judge.gate, judge.version)
        for name, judge in library().items()
        if switches.get(name, judge.on_by_default)
    ]
    seated += [Seated(spec) for spec in own]
    return [judge for judge in seated if case.simulated or judge.spec.on != "simulations"]


def gated(judge: Seated, case: Case) -> str | None:
    """Why the judge does not apply to this call, said before any model is asked; None if it may."""
    match judge.gate:
        case "irreversible-tools" if not _irreversible_ran(case):
            return "no tool this agent declares irreversible ran on this call"
        case "outbound" if case.direction != "outbound":
            return "the call came in: only an outbound call names who it calls for"
        case "expectations" if case.persona_rule is None:
            return "the simulated caller expected nothing of the agent"
        case _:
            return None


def facts_of(case: Case, around: Surroundings) -> str:
    """The facts a judge that reads them is told, one line each."""
    lines = [f"- organisation: {around.org}", f"- direction: {case.direction or 'unknown'}"]
    irreversible = sorted(
        name for name, spec in _declared_tools(case).items() if spec == "irreversible"
    )
    lines.append(f"- irreversible tools: {', '.join(irreversible) or 'none'}")
    lines += [
        f"- #{line.seq} {line.kind} {line.tool}" + (f" ({line.audience})" if line.audience else "")
        for line in case.gate
        if line.kind != "tool.call"
    ]
    lines.append(f"- an opt-out was written on this call: {'yes' if around.opted_out else 'no'}")
    if around.disclosure:
        lines.append(f'- the disclosure sentence outbound calls open with: "{around.disclosure}"')
    summary = case.summary or {}
    if summary.get("end_reason"):
        lines.append(f"- the call ended: {summary.get('end_reason')}, by {summary.get('ended_by')}")
    if case.persona_rule is not None:
        accepts, declines = case.persona_rule
        lines += ["- the simulated caller expected:"]
        lines += [f"  - {line}" for line in (accepts, declines) if line]
    return "\n".join(lines)


def _irreversible_ran(case: Case) -> bool:
    return any(
        line.kind == "tool.call" and line.side_effect == "irreversible" for line in case.gate
    )


def _declared_tools(case: Case) -> dict[str, str]:
    if case.declared is None:
        return {}
    return {name: spec.side_effect for name, spec in case.declared.tools_by_name.items()}


# The front matter is `key: value` lines; `reads` and `choices` are comma lists.
def _written(path: Path) -> Written:
    text = path.read_text(encoding="utf-8")
    _, separator, rest = text.partition("---\n")
    head, closing, body = rest.partition("\n---\n")
    if not separator or not closing:
        raise DeclarationRefused(NOT_A_PAGE.format(path=path.name))
    pairs = (line.partition(":") for line in head.splitlines())
    fields = {key.strip(): value.strip() for key, _, value in pairs}
    reads = {item.strip() for item in fields.get("reads", "").split(",") if item.strip()}
    spec = JudgeSpec(
        name=fields["name"],
        question=body.strip(),
        answer=_one_of(
            path, "answer", fields.get("answer", "verdict"), ("verdict", "choice", "score")
        ),
        choices=tuple(
            item.strip() for item in fields.get("choices", "").split(",") if item.strip()
        ),
        on=_one_of(path, "on", fields.get("on", "always"), ("always", "simulations", "trigger")),
        trigger=fields.get("trigger", ""),
        reads_prompt="prompt" in reads,
        reads_evidence="evidence" in reads,
        reads_facts="facts" in reads,
    )
    return Written(
        spec=spec,
        summary=fields.get("summary", ""),
        version=int(fields.get("version", "1")),
        on_by_default=fields.get("default", "off") == "on",
        gate=_one_of(path, "gate", fields.get("gate", "none"), GATES),
    )


def _one_of[T: str](path: Path, field: str, value: str, allowed: tuple[T, ...]) -> T:
    for each in allowed:
        if value == each:
            return each
    raise DeclarationRefused(
        NOT_A_FIELD.format(path=path.name, field=field, value=value, allowed=allowed)
    )

"""A golden played on a written call, and the judges its expectations set."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from pinecall.domain.errors import PinecallError
from pinecall.domain.names import JsonObject
from pinecall.evals.case import AGENT, CALLER, PUNCTUATION, Case, calls_of, said_by
from pinecall.evals.judges import CaseJudge, GoldenJudge, failing, passing
from pinecall.log.logs import Subscription
from pinecall.session import text
from pinecall.session.call import Lookup
from pinecall.session.session import Session
from pinecall.wire.commands import CallEvent
from pinecall.wire.events import MemoryOps
from pinecall.wire.parts import EndedBy, EndReason, MemoryFact, MemoryOp, PlatformTool
from pinecall.wire.rest.evals import EventStep, Golden, Register

# The wire has no "the app is done reacting": the log going quiet is the sign. Long enough for a
# render and its re-render to be one burst, short enough to cost little per turn.
QUIET_S = 0.15


# An app that never answers cannot hold a run up longer than this per step.
AT_MOST_S = 2.0


# The source of a golden's facts, so the log never passes them off as real memory.
A_GOLDEN = "golden"

# What a lookup writes on the call's log: the entry's type and its data.
type Writes = Callable[[str, JsonObject], Awaitable[object]]


APP_DETACHED: tuple[EndReason, EndedBy] = ("app_detached", "platform")


HUNG_UP: tuple[EndReason, EndedBy] = ("caller_hung_up", "caller")


# A golden the call refused (an event nobody declared) ends the call it opened.
BROKE: tuple[EndReason, EndedBy] = ("error", "platform")


HEARD = "Every line this golden puts in the caller's mouth reached the agent."


TOOLS = "Every tool this golden names was called in the conversation."


NOT_TOOLS = "The conversation called none of the tools this golden forbids."


SAYS = "The agent said every phrase this golden names."


SAYS_ANY = "The agent said at least one of the phrases this golden accepts."


SILENCE = "The agent said none of the phrases this golden names."


ANSWERED = "The agent answered every fact that arrived mid-call."


STAYED_QUIET = "The agent carried on without answering the facts that arrived mid-call."


# A check that could not look must not pass.
NO_EVENT = "this golden expects a reply to an event, and no event.received reached the call"


REGISTER = "The agent addressed the caller as {register} in every one of its turns."


ON_THE_PANEL = "The judge {name} held of this call."


# A check that could not look must not pass: a judge nobody wrote breaks the golden.
NOT_ON_THE_PANEL = "this golden asks {name}, and there is no such judge: there are {panel}"


# Unmistakable tú; `té` (tea) carries its accent, so `te` never matches it.
TUTEO: frozenset[str] = frozenset(
    {"tú", "ti", "te", "contigo", "tu", "tus", "tuyo", "tuya", "tuyos", "tuyas"}
)


# `le`, `les`, `su` and `sus` are left out: they are as often the third person.
USTEO: frozenset[str] = frozenset({"usted", "ustedes", "consigo", "suyo", "suya", "suyos", "suyas"})


@dataclass(frozen=True)
class Played:
    """A golden played to its end, or to where the app let go of the agent."""

    held: bool
    # The requests the model was sent, one per request, as they went.
    requests: tuple[JsonObject, ...]


# `panel` is every judge the agent's calls may meet, the library's and the org's own, by name.
def golden_judges(
    golden: Golden, case: Case, panel: Mapping[str, GoldenJudge] | None = None
) -> list[GoldenJudge]:
    """Consent, one judge per expectation set, then the judges it names, from `panel`."""
    expect = golden.expect
    panel = panel or {}
    judges: list[GoldenJudge] = [panel["consent"]] if "consent" in panel else []
    # Without it an unheard caller would pass every "never did X".
    if golden.input:
        judges.append(_heard(case, len(golden.input)))
    if expect.tools:
        judges.append(_tools(case, expect.tools))
    if expect.not_tools:
        judges.append(_not_tools(case, expect.not_tools))
    if expect.not_said:
        judges.append(_silence(case, expect.not_said))
    if expect.says:
        judges.append(_says(case, expect.says))
    if expect.says_any:
        judges.append(_says_any(case, expect.says_any))
    if expect.grounded:
        judges.append(_on_the_panel(panel, "grounded"))
    if expect.addressed_as is not None:
        judges.append(_register_judge(case, expect.addressed_as))
    if expect.replies is not None:
        judges.append(_replies(case, replies=expect.replies))
    present = {judge.name for judge in judges}
    judges.extend(_on_the_panel(panel, name) for name in expect.judges if name not in present)
    return judges


def events_after(golden: Golden, turn: int) -> tuple[EventStep, ...]:
    """The facts injected after this many caller lines, in the order the golden lists them."""
    return tuple(event for event in golden.events if event.after_turn == turn)


# recall reads the golden's facts and nothing else; search is the real index, since that is what
# the golden is asking about. The recall is written as a real one is, so the app learns the facts.
def golden_lookup(facts: Sequence[str], search: Lookup, wrote: Writes) -> Lookup:
    """The lookup a golden's call runs: recall answered by its facts, search by the index."""

    async def lookup(tool: PlatformTool, arguments: JsonObject, speech: str | None) -> JsonObject:
        if tool != "recall":
            return await search(tool, arguments, speech)
        recalled = [MemoryFact(text=fact, source=A_GOLDEN) for fact in facts]
        query = arguments.get("query")
        op = MemoryOp(op="recall", query=str(query) if query else None, facts=recalled, took_ms=0.0)
        await wrote("memory.ops", MemoryOps(ops=[op], speech_id=speech).written())
        return {"facts": [{"text": fact, "source": A_GOLDEN} for fact in facts]}

    return lookup


# The call opens with no greeting (a run starts mid-conversation); the facts go through the
# app's own `call.event`, so an event the agent never declared is refused as on a real call.
async def drive(
    session: Session, golden: Golden, heard: Subscription, *, is_held: Callable[[], bool]
) -> Played:
    """Start the call, seed its state, say each line and inject each fact, then hang up."""
    await session.start()
    try:
        kept_on = await _played(session, golden, heard, is_held=is_held)
    except PinecallError:
        await text.end(session, *BROKE)
        raise
    await text.end(session, *(HUNG_UP if kept_on else APP_DETACHED))
    return Played(held=kept_on, requests=tuple(session.call.requests))


async def settled(heard: Subscription, *, quiet_s: float = QUIET_S) -> None:
    """Return once nothing reached the log for `quiet_s`, and always within AT_MOST_S."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + AT_MOST_S
    while (left := deadline - loop.time()) > 0:
        try:
            await asyncio.wait_for(anext(heard), min(quiet_s, left))
        except (TimeoutError, StopAsyncIteration):
            return


async def _played(
    session: Session, golden: Golden, heard: Subscription, *, is_held: Callable[[], bool]
) -> bool:
    await settled(heard)
    for turn, line in enumerate([*golden.input, None]):
        for event in events_after(golden, turn):
            await session.apply(CallEvent(name=event.name, data=dict(event.data)))
            await settled(heard)
        if not is_held():
            return False
        if line is None:
            break
        await text.hears(session, line)
        await settled(heard)
    return True


def _on_the_panel(panel: Mapping[str, GoldenJudge], name: str) -> GoldenJudge:
    """The judge of that name, or one that breaks saying there is none."""
    found = panel.get(name)
    if found is not None:
        return found
    names = ", ".join(panel) or "no judge at all"
    ruling = failing(NOT_ON_THE_PANEL.format(name=name, panel=names))
    return CaseJudge(name, ON_THE_PANEL.format(name=name), ruling)


def _register_judge(case: Case, expected: Register) -> CaseJudge:
    """Whether the agent never used the other register's words."""
    other = USTEO if expected == "tu" else TUTEO
    turns = said_by(case, AGENT)
    slips = [
        (number, word) for number, turn in enumerate(turns, 1) for word in _marked(turn, other)
    ]
    if slips:
        spoken = "; ".join(f"{word!r} in agent turn {number}" for number, word in slips)
        ruling = failing(f"the agent was asked for {expected} and said {spoken}")
    else:
        ruling = passing(
            f"no word of the other register in {len(turns)} agent turn(s), asked for {expected}"
        )
    return CaseJudge("register", REGISTER.format(register=expected), ruling)


def _heard(case: Case, lines: int) -> CaseJudge:
    heard = len(said_by(case, CALLER))
    if heard < lines:
        word = "line" if lines == 1 else "lines"
        ruling = failing(
            f"the golden says {lines} {word} and the agent heard {heard}: "
            "whatever else this call did, it was not this golden"
        )
    else:
        ruling = passing(f"the agent heard all {lines} of the caller's lines")
    return CaseJudge("heard", HEARD, ruling)


# The order the tools ran in is not judged.
def _tools(case: Case, names: Sequence[str]) -> CaseJudge:
    ran = {called.name for called in calls_of(case)}
    missing = [name for name in names if name not in ran]
    if missing:
        what_ran = ", ".join(sorted(ran)) or "no tool at all"
        ruling = failing(f"the golden expects {', '.join(missing)}, and this call ran {what_ran}")
    else:
        ruling = passing(f"every expected tool ran: {', '.join(names)}")
    return CaseJudge("tools", TOOLS, ruling)


def _not_tools(case: Case, names: Sequence[str]) -> CaseJudge:
    forbidden = frozenset(names)
    slips = [
        f"{line.tool} at seq {line.seq}"
        for line in case.gate
        if line.kind == "tool.call" and line.tool in forbidden
    ]
    if slips:
        ruling = failing(
            f"the golden forbids {', '.join(names)}, and this call ran {'; '.join(slips)}"
        )
    else:
        ruling = passing(f"none of the {len(names)} forbidden tool(s) ran")
    return CaseJudge("not_tools", NOT_TOOLS, ruling)


def _says(case: Case, phrases: Sequence[str]) -> CaseJudge:
    turns = [turn.casefold() for turn in said_by(case, AGENT)]
    missing = [phrase for phrase in phrases if not any(phrase.casefold() in turn for turn in turns)]
    if missing:
        ruling = failing(f"the agent never said {', '.join(repr(phrase) for phrase in missing)}")
    else:
        ruling = passing(f"the agent said all {len(phrases)} expected phrase(s)")
    return CaseJudge("says", SAYS, ruling)


def _says_any(case: Case, phrases: Sequence[str]) -> CaseJudge:
    turns = [turn.casefold() for turn in said_by(case, AGENT)]
    found = [phrase for phrase in phrases if any(phrase.casefold() in turn for turn in turns)]
    if found:
        ruling = passing(f"the agent said {found[0]!r}, one of the {len(phrases)} accepted")
    else:
        accepted = ", ".join(repr(phrase) for phrase in phrases)
        ruling = failing(f"the agent said none of {accepted}")
    return CaseJudge("says_any", SAYS_ANY, ruling)


def _silence(case: Case, phrases: Sequence[str]) -> CaseJudge:
    turns = [turn.casefold() for turn in said_by(case, AGENT)]
    slips = [
        f"{phrase!r} in agent turn {number}"
        for phrase in phrases
        for number, turn in enumerate(turns, 1)
        if phrase.casefold() in turn
    ]
    if slips:
        ruling = failing(f"the golden forbids these and the agent said {'; '.join(slips)}")
    else:
        ruling = passing(f"none of the {len(phrases)} forbidden phrase(s) was said")
    return CaseJudge("silence", SILENCE, ruling)


# Whether the agent took the fact up, not when: the timing is the app's.
def _replies(case: Case, *, replies: bool) -> CaseJudge:
    criteria = ANSWERED if replies else STAYED_QUIET
    if not case.arrived:
        return CaseJudge("replies", criteria, failing(NO_EVENT))
    findings: list[str] = []
    for fact in case.arrived:
        after = next(
            (turn for turn in case.turns if turn.role == AGENT and turn.seq > fact.seq), None
        )
        carried = [str(value) for value in fact.data.values() if str(value)]
        named = after is not None and (
            not carried or any(value.casefold() in after.text.casefold() for value in carried)
        )
        if replies and after is None:
            findings.append(f"{fact.name} at seq {fact.seq} was followed by no turn of the agent's")
        elif replies and not named:
            findings.append(f"the agent's turn after {fact.name} names nothing the event carried")
        elif not replies and named:
            findings.append(f"the agent took {fact.name} up, and this golden expects it quiet")
    if findings:
        return CaseJudge("replies", criteria, failing("; ".join(findings)))
    kept = "taken up" if replies else "left alone"
    return CaseJudge(
        "replies", criteria, passing(f"all {len(case.arrived)} fact(s) that arrived were {kept}")
    )


# Whole words only: `tu` is not `tutor`.
def _marked(turn: str, markers: frozenset[str]) -> list[str]:
    found: list[str] = []
    for word in turn.split():
        bare = word.strip(PUNCTUATION).casefold()
        if bare in markers and bare not in found:
            found.append(bare)
    return found

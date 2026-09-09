"""What a golden expects, as judges: five questions, all answered by code, none by a model."""

from __future__ import annotations

from collections.abc import Sequence
from typing import override

from livekit.agents.evals import JudgmentResult
from livekit.agents.llm import ChatContext

from pinecall.evals.case import AGENT, Arrived, Case, Said
from pinecall.evals.judges.policy import PolicyJudge, broken, held
from pinecall.evals.transcript import said_by_the_agent, tools_called
from pinecall.types import GateLine

TOOLS = "Every tool this golden names was called in the conversation."
NOT_TOOLS = "The conversation called none of the tools this golden forbids."
SAYS = "The agent said every phrase this golden names."
SILENCE = "The agent said none of the phrases this golden names."
ANSWERED = "The agent answered every fact that arrived mid-call."
STAYED_QUIET = "The agent carried on without answering the facts that arrived mid-call."

# A golden that declares `replies` and injects no event is asking about something that never
# happened. Broken rather than held: a check that could not look must never read as proof.
NO_EVENT = "this golden expects a reply to an event, and no event.received reached the call"


class EveryToolRanJudge(PolicyJudge):
    """Did the conversation call each of these tools, whatever else it also called."""

    def __init__(self, names: Sequence[str]) -> None:
        super().__init__(name="tools", criteria=TOOLS)
        self._names = tuple(names)

    @override
    def decide(self, chat_ctx: ChatContext) -> JudgmentResult:
        """The tools this call made, against the ones the golden names. Order is not asked."""
        called = frozenset(tools_called(chat_ctx))
        missing = [name for name in self._names if name not in called]
        if missing:
            ran = ", ".join(sorted(called)) or "no tool at all"
            return broken(f"the golden expects {', '.join(missing)}, and this call ran {ran}")
        return held(f"every expected tool ran: {', '.join(self._names)}")


# The mirror of the judge above, and the only thing in ring 1 that catches a booking made before
# the caller's yes while the confirmation gate is deferred (docs/decisions/pinecall-test.md): a
# phrase list is about WORDS, and a model that books while saying "voy a reservarle la cita" says
# none of the forbidden ones. This one is about the log.
class NoForbiddenToolRanJudge(PolicyJudge):
    """The mirror of `EveryToolRanJudge`: did the call keep off every tool the golden forbids."""

    # A break must name the seq of the call, and livekit's items carry no seq — a ChatContext is a
    # conversation, not a log. So the gate's own trace comes in at construction, the way consent
    # takes it: it already carries every `tool.call` of the call with the number the log gave it.
    def __init__(self, names: Sequence[str], gate: Sequence[GateLine]) -> None:
        super().__init__(name="not_tools", criteria=NOT_TOOLS)
        self._names = tuple(names)
        self._gate = tuple(gate)

    @override
    def decide(self, chat_ctx: ChatContext) -> JudgmentResult:  # noqa: ARG002 — the log, not the words
        """Every forbidden tool that ran, named with the seq a reader opens the log at."""
        forbidden = frozenset(self._names)
        slips = [
            f"{line.tool} at seq {line.seq}"
            for line in self._gate
            if line.kind == "tool.call" and line.tool in forbidden
        ]
        if slips:
            forbids = ", ".join(self._names)
            return broken(f"the golden forbids {forbids}, and this call ran {'; '.join(slips)}")
        return held(f"none of the {len(self._names)} forbidden tool(s) ran")


class EveryPhraseWasSaidJudge(PolicyJudge):
    """Did the agent say each of these, somewhere in its own turns."""

    def __init__(self, phrases: Sequence[str]) -> None:
        super().__init__(name="says", criteria=SAYS)
        self._phrases = tuple(phrases)

    @override
    def decide(self, chat_ctx: ChatContext) -> JudgmentResult:
        """Each phrase somewhere in the agent's own turns, whichever turn it landed in."""
        said = _folded(chat_ctx)
        missing = [phrase for phrase in self._phrases if not _anywhere_in(phrase, said)]
        if missing:
            return broken(f"the agent never said {', '.join(repr(one) for one in missing)}")
        return held(f"the agent said all {len(self._phrases)} expected phrase(s)")


class NothingWasSaidJudge(PolicyJudge):
    """The mirror: did the agent keep off every one of these."""

    def __init__(self, phrases: Sequence[str]) -> None:
        super().__init__(name="silence", criteria=SILENCE)
        self._phrases = tuple(phrases)

    @override
    def decide(self, chat_ctx: ChatContext) -> JudgmentResult:
        """None of the phrases in any agent turn. The turn is named, because one turn is the bug."""
        said = _folded(chat_ctx)
        slips = [
            f"{phrase!r} in agent turn {number}"
            for phrase in self._phrases
            for number, turn in enumerate(said, 1)
            if phrase.casefold() in turn
        ]
        if slips:
            return broken(f"the golden forbids these and the agent said {'; '.join(slips)}")
        return held(f"none of the {len(self._phrases)} forbidden phrase(s) was said")


# Mentioning it, not speaking first: whether the agent takes a fact up the moment it arrives or on
# the caller's next question is the app's business, and a golden that judged the order would be
# judging how the tenant wired its own handler rather than what the caller was told.
class TheEventWasAnsweredJudge(PolicyJudge):
    """One question about the facts that arrived mid-call: did the agent take them up, or not."""

    # Like consent, this one is about WHERE in the log a turn landed, and livekit's items carry no
    # seq to place them by. So the call's own rows come in at construction and the transcript is
    # not read: `Said.seq` is the log's numbering, and it is the whole question here.
    def __init__(self, replies: bool, case: Case) -> None:
        super().__init__(name="replies", criteria=ANSWERED if replies else STAYED_QUIET)
        self._replies = replies
        self._case = case

    @override
    def decide(self, chat_ctx: ChatContext) -> JudgmentResult:  # noqa: ARG002 — the seqs, not the words
        """For every fact that arrived: whether the agent's next turn took it up, or ignored it."""
        arrived = self._case.events
        if not arrived:
            return broken(NO_EVENT)
        findings = [reason for fact in arrived if (reason := self._mishandled(fact)) is not None]
        if findings:
            return broken("; ".join(findings))
        kept = "taken up" if self._replies else "left alone"
        return held(f"all {len(arrived)} fact(s) that arrived were {kept}")

    def _mishandled(self, fact: Arrived) -> str | None:
        """What is wrong with how the agent took this fact, or None when nothing is."""
        reply = self._the_agent_turn_after(fact.seq)
        if self._replies and reply is None:
            return f"{fact.name} at seq {fact.seq} was followed by no turn of the agent's at all"
        named = reply is not None and _names(reply, fact)
        if self._replies and not named:
            return f"the agent's turn after {fact.name} names nothing the event carried"
        if not self._replies and named:
            return f"the agent took {fact.name} up, and this golden expects it to stay quiet"
        return None

    def _the_agent_turn_after(self, seq: int) -> Said | None:
        """The first thing the agent said after that entry. None when it never spoke again."""
        later = [turn for turn in self._case.turns if turn.role == AGENT and turn.seq > seq]
        return later[0] if later else None


# An event with no data has nothing to be named by, so answering at all is the whole answer.
def _names(turn: Said, fact: Arrived) -> bool:
    """Whether the agent's turn carries anything the event brought with it."""
    said = turn.text.casefold()
    carried = [str(value) for value in fact.data.values() if str(value)]
    return not carried or any(value.casefold() in said for value in carried)


def _folded(chat_ctx: ChatContext) -> tuple[str, ...]:
    """The agent's turns, casefolded: a phrase is matched the way a person reads it, not by case."""
    return tuple(said.casefold() for said in said_by_the_agent(chat_ctx))


def _anywhere_in(phrase: str, said: Sequence[str]) -> bool:
    """Whether one phrase appears in any of those turns."""
    return any(phrase.casefold() in turn for turn in said)

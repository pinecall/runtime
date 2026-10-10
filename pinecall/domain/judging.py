"""A judge: one question a model answers about a finished call, whoever wrote it."""

import re
from dataclasses import dataclass
from typing import Literal

from pinecall.domain.errors import DeclarationRefused

# held/broken, one of the listed choices, or a score from 1 to 5.
type JudgeAnswer = Literal["verdict", "choice", "score"]


# Every call the org judges, only a call a persona played, or a call a short first question says
# the judge applies to (a no is N/A, and the judge is never asked).
type JudgeOn = Literal["always", "simulations", "trigger"]


# The name call.score gives the answer: lower-case words joined by hyphens.
A_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


# Fewer than two is not a choice.
FEWEST_CHOICES = 2


NOT_A_NAME = (
    "{name!r} is not a judge's name: lower-case words joined by hyphens, like offers-next-slot"
)
NO_QUESTION = "a judge asks a question of a call: write one"
NO_CHOICES = "a judge that answers one of several needs at least two choices"
NO_TRIGGER = "a judge that runs on a trigger needs the trigger's question"
CHOICES_ONLY = "only a judge that answers one of several takes choices"


@dataclass(frozen=True)
class JudgeSpec:
    """One judge: its name, its question, how it answers, when it runs and what it reads."""

    name: str
    question: str
    answer: JudgeAnswer = "verdict"
    choices: tuple[str, ...] = ()
    on: JudgeOn = "always"
    trigger: str = ""
    # The call and its tool calls are always read; these add the agent's prompt, the evidence the
    # call carried (knowledge, documents searched, facts recalled) and the facts about the call.
    reads_prompt: bool = False
    reads_evidence: bool = False
    reads_facts: bool = False

    def __post_init__(self) -> None:
        if not A_NAME.match(self.name):
            raise DeclarationRefused(NOT_A_NAME.format(name=self.name))
        if not self.question.strip():
            raise DeclarationRefused(NO_QUESTION)
        if self.answer == "choice" and len(self.choices) < FEWEST_CHOICES:
            raise DeclarationRefused(NO_CHOICES)
        if self.answer != "choice" and self.choices:
            raise DeclarationRefused(CHOICES_ONLY)
        if self.on == "trigger" and not self.trigger.strip():
            raise DeclarationRefused(NO_TRIGGER)

    @property
    def reads(self) -> tuple[str, ...]:
        """What the judge reads beyond the call, by the names the wire and the store use."""
        named = (
            ("prompt", self.reads_prompt),
            ("evidence", self.reads_evidence),
            ("facts", self.reads_facts),
        )
        return tuple(name for name, read in named if read)

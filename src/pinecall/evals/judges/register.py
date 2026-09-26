"""The register judge: whether the agent addressed the caller as tú or as usted, decided by code."""

from __future__ import annotations

from typing import override

from livekit.agents.evals import JudgmentResult
from livekit.agents.llm import ChatContext

from pinecall.evals.goldens import Register
from pinecall.evals.judges.code_judge import PolicyJudge, broken, held
from pinecall.evals.transcript import said_by_the_agent
from pinecall.evals.words import PUNCTUATION

# Words that can only be addressed to the listener as tú. `te`, `tu` and `tus` are second person
# and nothing else; `té` carries its accent, so scanning for `te` never catches the drink.
TUTEO: frozenset[str] = frozenset(
    {"tú", "ti", "te", "contigo", "tu", "tus", "tuyo", "tuya", "tuyos", "tuyas"}
)

# The other half, and deliberately shorter: `le`, `les`, `su` and `sus` are third person as often
# as they are polite ("su cita con la doctora"), so a scan that counted them would report a
# register the agent never chose. What is left only ever addresses the listener.
USTEO: frozenset[str] = frozenset({"usted", "ustedes", "consigo", "suyo", "suya", "suyos", "suyas"})

MARKERS: dict[str, frozenset[str]] = {"tu": TUTEO, "usted": USTEO}


CRITERIA = "The agent addressed the caller as {register} in every one of its turns."


class RegisterJudge(PolicyJudge):
    """One question about the register the business asked for, answered word by word by code."""

    def __init__(self, expected: Register) -> None:
        super().__init__(name="register", criteria=CRITERIA.format(register=expected))
        self._expected = expected

    @override
    def decide(self, chat_ctx: ChatContext) -> JudgmentResult:
        """No word of the other register in any agent turn. A turn marking neither is no fault."""
        other = MARKERS["usted" if self._expected == "tu" else "tu"]
        said = said_by_the_agent(chat_ctx)
        slips = [
            (number, word) for number, turn in enumerate(said, 1) for word in _marked(turn, other)
        ]
        if slips:
            spoken = "; ".join(f"{word!r} in agent turn {number}" for number, word in slips)
            return broken(f"the agent was asked for {self._expected} and said {spoken}")
        return held(
            f"no word of the other register in {len(said)} agent turn(s), "
            f"asked for {self._expected}"
        )


# Whole words, because `tu` is a register and `tutor` is a person, and a substring scan over a
# transcript fails on its own examples.
def _marked(said: str, markers: frozenset[str]) -> list[str]:
    """The marker words this turn used, in the order they were said, each named once."""
    seen: list[str] = []
    for word in said.split():
        stripped = word.strip(PUNCTUATION).casefold()
        if stripped in markers and stripped not in seen:
            seen.append(stripped)
    return seen

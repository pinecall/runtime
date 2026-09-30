"""What the ears are told: the words that only agree, the pause before a barge-in, the keyterms."""

import re
from dataclasses import dataclass

from pinecall.domain.agent import AgentConfig, Turn
from pinecall.domain.names import JsonObject

# livekit counts no words by default; one word is as often a cough or an echo.
MIN_WORDS = 2


# Silence before an interruption is judged false: livekit's 2 s sound like a dropped call.
FALSE_INTERRUPTION_TIMEOUT_S = 1.0


# A turn made of these alone is somebody agreeing, and does not take the floor. livekit's own
# backchannel detector is hosted only. Per language: "vale" takes the floor in English.
BACKCHANNELS: dict[str, frozenset[str]] = {
    "es": frozenset(
        {
            "aha", "ajá", "ah", "ajam", "bien", "bueno", "claro", "dale", "eh", "em", "exacto",
            "hm", "hmm", "mm", "mmm", "ok", "okay", "perfecto", "sí", "si", "vale", "ya", "uh",
            "uhu", "uhum",
        }
    ),
    "en": frozenset(
        {
            "aha", "ah", "hm", "hmm", "mm", "mmm", "mhm", "ok", "okay", "yeah", "yes", "yep",
            "right", "sure", "uh", "uh-huh", "gotcha", "exactly",
        }
    ),
    "pt": frozenset({"aham", "ah", "sim", "tá", "certo", "claro", "ok", "beleza", "hum", "uhum"}),
    "ca": frozenset({"sí", "vale", "d'acord", "clar", "entesos", "ok", "mm", "aha", "eh"}),
}  # fmt: skip


_A_WORD = re.compile(r"[^\W_]+(?:['\u2019-][^\W_]+)*")


# Keyterms are names, not sentences: longer text dilutes them. The cap is ours, since the ears'
# vendors either document none or count the terms against the model's budget.
LONGEST_TERM = 40


MOST_WORDS = 4


MOST_TERMS = 50


@dataclass(frozen=True)
class TurnPolicy:
    """What the caller must say to take the floor, in the agent's language."""

    backchannels: frozenset[str]
    min_words: int = MIN_WORDS
    false_interruption_s: float = FALSE_INTERRUPTION_TIMEOUT_S
    # How long the caller speaks over the agent before it stops; None leaves livekit's own.
    min_speech_s: float | None = None

    def is_a_backchannel(self, text: str) -> bool:
        """Whether every word said is somebody agreeing."""
        words: list[str] = [str(found.group(0)).lower() for found in _A_WORD.finditer(text)]
        return bool(words) and all(word in self.backchannels for word in words)


def policy_for(language: str | None, turn: Turn | None = None) -> TurnPolicy:
    """The policy of the agent's language (`es-ES` is `es`) and its turn knobs; else every list."""
    tag = (language or "").split("-", 1)[0].lower()
    words = BACKCHANNELS.get(tag) or frozenset[str]().union(*BACKCHANNELS.values())
    declared = turn or Turn()
    return TurnPolicy(
        backchannels=words,
        min_words=MIN_WORDS
        if declared.min_interruption_words is None
        else declared.min_interruption_words,
        min_speech_s=None
        if declared.min_interruption_ms is None
        else declared.min_interruption_ms / 1000,
    )


# The declared words first, so the cap drops the names found in the state before them. One
# level deep catches `patient = {name, phone}`; a term needs a letter, which leaves out numbers.
def keyterms(config: AgentConfig, state: JsonObject) -> list[str]:
    """The words the ears are told to expect: the agent's own, then names the state holds."""
    found: list[object] = []
    for value in state.values():
        found += list(value.values()) if isinstance(value, dict) else [value]
    names = [name for name in (_a_name(item) for item in found) if name]
    return list(dict.fromkeys(term for term in (*config.hears, *names) if term))[:MOST_TERMS]


def _a_name(value: object) -> str:
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if not name or len(name) > LONGEST_TERM or len(name.split()) > MOST_WORDS:
        return ""
    return name if any(letter.isalpha() for letter in name) else ""

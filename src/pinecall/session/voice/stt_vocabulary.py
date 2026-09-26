"""What the ears are told to expect: the words the agent declared, and the names its state holds."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pinecall.providers.registry import Ears
from pinecall.types import AgentConfig

# A keyterm is a name, not a sentence: "doctora Vidal" helps the model, a paragraph of notes only
# dilutes it. Both numbers are ours and both are generous — the shortest street name here is two
# words and the longest doctor's name fits in forty characters.
LONGEST_TERM = 40
MOST_WORDS = 4

# What the vendors will read before they start ignoring the tail. Deepgram documents no hard limit
# and Soniox counts context against the model's own budget, so the cap is ours: fifty names is
# already more than a clinic's whole staff, and a list past that is a bug in the app's state.
MOST_TERMS = 50


def takes_keyterms(ears: Ears | None) -> bool:
    """Whether these ears have livekit's generic keyterms door at all (stt/stt.py:139,293)."""
    return ears is not None and ears.capabilities.keyterms


# The declared words come first because the tenant wrote them on purpose, and a cap that has to
# cut something cuts the state's guesses rather than the declaration.
def words(config: AgentConfig, state: Mapping[str, Any] | None = None) -> list[str]:
    """Everything the ears should expect right now: `hears`, then the names the state is holding."""
    said = list(config.hears)
    said.extend(_names_in(state or {}))
    return list(dict.fromkeys(term for term in said if term))[:MOST_TERMS]


# One level into the state's objects, because an app writes `patient = { name, phone }` and the
# name is what the caller will say next. Deeper than that is a record, not a name.
def _names_in(state: Mapping[str, Any]) -> list[str]:
    """Every value of the app's state that reads like a proper name, in the order it was written."""
    found: list[str] = []
    for value in state.values():
        if isinstance(value, Mapping):
            found.extend(_a_name(inner) for inner in value.values())  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        else:
            found.append(_a_name(value))
    return [name for name in found if name]


# A phone number and an id are strings too, and neither is a word anybody pronounces: a keyterm
# has to contain a letter or it is teaching the model nothing it does not already hear.
def _a_name(value: Any) -> str:
    """The value as a keyterm, or empty when it is not the kind of thing a caller says."""
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if not name or len(name) > LONGEST_TERM or len(name.split()) > MOST_WORDS:
        return ""
    return name if any(letter.isalpha() for letter in name) else ""

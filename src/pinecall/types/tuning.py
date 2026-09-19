"""An agent's tuning and the org's lexicon: what the org set over the declaration, as shapes."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from pinecall.types.agent import Greeting, Hangup, Turn, pronunciations_checked
from pinecall.types.knowledge import Docs, MemoryPolicy
from pinecall.types.refused import DeclarationRefused

# convo ms-14: an empty voice reached the vendor and a whole line of calls went out silent, because
# "" is a value and None is not. A knob nobody wants set is LEFT OUT — None — and never sent as an
# empty string. It is the shape's own rule, so every door that builds one refuses the same way.
BLANK = (
    "a blank {field} is not a change: an empty value once silenced a whole line of calls. "
    "Leave the field out to go back to what the app declared."
)

# The five knobs that name a vendor, a model or a voice: the ones a blank would reach a vendor as.
NAMED_KNOBS = ("voice", "tts", "tts_model", "stt", "llm")


# What the org set for one agent, in one world, in one corner, as one version says it. Everything
# the class used to declare and a deploy used to change: which vendors and models, how the call
# opens and ends, how a turn is cut, what is remembered, which bases are read. None, or empty, is
# a knob nobody set — the app's own declaration, or the runtime's default, stands for it.
@dataclass(frozen=True)
class Tuning:
    """One agent's tuning: every knob None, or empty, until the org set it."""

    voice: str | None = None
    tts: str | None = None
    tts_model: str | None = None
    stt: str | None = None
    llm: str | None = None
    greeting: Greeting | None = None
    hangup: Hangup | None = None
    turn: Turn | None = None
    memory: MemoryPolicy | None = None
    knowledge: tuple[Docs, ...] = ()

    def __post_init__(self) -> None:
        for name in NAMED_KNOBS:
            value: str | None = getattr(self, name)
            if value is not None and not value.strip():
                raise DeclarationRefused(BLANK.format(field=name))


# The org's words, laid over every agent's own: a brand, a surname, an acronym is the same word
# whichever agent says it, and the person who hears it said wrong is not the person who deploys.
@dataclass(frozen=True)
class Lexicon:
    """The org's lexicon: how the voice says its words, and which ones the ears must know."""

    said: Mapping[str, str] = field(default_factory=dict[str, str])
    heard: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        pronunciations_checked(self.said, "the lexicon")
        if any(not word.strip() for word in self.heard):
            raise DeclarationRefused("the lexicon: a word the ears must know is not blank")


@dataclass(frozen=True)
class Kept[T]:
    """One version as it is kept: whose corner, which number, who set it, when, and what it says."""

    holder: str
    version: int
    author: str
    note: str | None
    set_at: datetime
    value: T


# A call's head row keeps these two numbers, so what a call ran on is never a guess: the exact
# rows can be read back by them. None is a corner that had set nothing when the call opened.
@dataclass(frozen=True)
class Versions:
    """Which tuning and which lexicon a call was built on; None where nothing was set."""

    config: int | None = None
    lexicon: int | None = None

"""What the agent knows beyond its instructions: the docs it retrieves and what it remembers."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, get_args

from pinecall.types.refused import DeclarationRefused

# retrieved: the best chunks are put in front of the model every turn, without asking it.
# tool: the model gets a search(query) tool and decides when to look.
type DocsMode = Literal["retrieved", "tool"]

DOCS_MODES: frozenset[str] = frozenset(get_args(DocsMode.__value__))

# How one FILE of a base reaches the model: cut into chunks a turn searches, or whole, in the
# static knowledge block of every call — the file the class used to carry by heart, as a
# document of the base instead (0038).
type FileMode = Literal["retrieved", "whole"]

FILE_MODES: frozenset[str] = frozenset(get_args(FileMode.__value__))

# The design's budget: thirty candidates per branch, RRF, and eight chunks to the model.
DEFAULT_CHUNKS_PER_TURN = 8

# Past this many tokens a base's whole files are called heavy: every call of every agent reading
# the base carries them in its prompt, cached but paid for, and the first turn waits on them. A
# push past it lands all the same and is told so; the number is a notice, never a refusal.
HEAVY_WHOLE_TOKENS = 8_000


@dataclass(frozen=True)
class Docs:
    """The indexed corpus the agent answers from, and how it reaches the model."""

    base: str
    mode: DocsMode = "retrieved"
    k: int = DEFAULT_CHUNKS_PER_TURN
    min_score: float | None = None

    def __post_init__(self) -> None:
        if not self.base:
            raise DeclarationRefused("docs name the knowledge base they were pushed to")
        if self.mode not in DOCS_MODES:
            raise DeclarationRefused(f"docs mode is one of {sorted(DOCS_MODES)}, not {self.mode!r}")
        if self.k < 1:
            raise DeclarationRefused("docs hand the model at least one chunk")
        if self.min_score is not None and self.min_score < 0:
            raise DeclarationRefused("a fused rank score is never negative; min_score cannot be")


@dataclass(frozen=True)
class KnowledgeFile:
    """One file of knowledge: its path as the tenant keeps it, its text, and its mode."""

    path: str
    text: str
    mode: FileMode = "retrieved"

    def __post_init__(self) -> None:
        if not self.path:
            raise DeclarationRefused("a knowledge file is named by its path")
        if self.mode not in FILE_MODES:
            raise DeclarationRefused(
                f"a file's mode is one of {sorted(FILE_MODES)}, not {self.mode!r}"
            )


# The tenant says what is worth keeping in its own words: "how they like to be addressed",
# "allergies". What it lists under forget is never written, whatever the model extracts.
@dataclass(frozen=True)
class MemoryPolicy:
    """What memory keeps about a contact across calls, and what it never keeps."""

    remember: tuple[str, ...] = ()
    forget: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if both := set(self.remember) & set(self.forget):
            raise DeclarationRefused(f"memory cannot both remember and forget {sorted(both)}")


# Bi-temporal: a fact is never deleted, it is superseded — `invalidated_at` says when, and the
# history of a contact is every row, current first.
@dataclass(frozen=True)
class Fact:
    """One thing memory holds about a contact: what, which kind, since when, how well it matched."""

    id: str
    contact: str
    text: str
    category: str | None
    source: str | None
    valid_from: datetime
    invalidated_at: datetime | None
    score: float


@dataclass(frozen=True)
class Chunk:
    """One piece of a knowledge base as retrieval hands it back: where, and how well it matched."""

    id: str
    base: str
    path: str
    heading: str | None
    text: str
    score: float

"""What the agent knows beyond its instructions: the docs it retrieves and what it remembers."""

from dataclasses import dataclass
from typing import Literal, get_args

from pinecall.types.refused import DeclarationRefused

# retrieved: the best chunks are put in front of the model every turn, without asking it.
# tool: the model gets a search(query) tool and decides when to look.
type DocsMode = Literal["retrieved", "tool"]

DOCS_MODES: frozenset[str] = frozenset(get_args(DocsMode.__value__))

# The design's budget: thirty candidates per branch, RRF, and eight chunks to the model.
DEFAULT_CHUNKS_PER_TURN = 8


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

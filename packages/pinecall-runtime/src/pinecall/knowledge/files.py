"""A file of a base as a listing draws it, and what a search says across two embedders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# What a search says when the vectors in the table and the vectors this gateway makes came out of
# two different models: they are numbers of the same width and nothing else, and ranking one
# against the other is a plausible answer with no meaning in it. The way out is in the sentence.
PUSHED_WITH_ANOTHER_MODEL = (
    "base {base} was pushed with {pushed}; this gateway embeds with {mine}: push it again"
)


@dataclass(frozen=True)
class File:
    """One file of a base as a listing draws it: its path, its size, what it became, and when."""

    path: str
    chars: int
    chunks: int
    pushed_at: datetime
    # The whole text, when one file was asked for; a listing carries the size and not the text.
    text: str | None = None

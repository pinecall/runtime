"""A marker: the line a view writes and never resolves, and the fill that takes its place."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, get_args

from pinecall.types.refused import DeclarationRefused

# Who fills a marker: the contact's facts and the knowledge base's chunks per turn, the one file
# the agent knows by heart once per call. The wire's MarkerName is the same three words.
type MarkerName = Literal["memory", "retrieved", "knowledge"]

MARKER_NAMES: frozenset[str] = frozenset(get_args(MarkerName.__value__))

# `<!-- memory: {"kinds":["preference"],"limit":6} -->`, on a line of its own, exactly as both
# frameworks write it (agents: views/components.ts `marker`, ruby: view.rb `marker`). The payload
# is everything between the colon and the closing arrow, and the line is kept whole because the
# fills are keyed by it.
A_MARKER = re.compile(rf"^\s*<!--\s*({'|'.join(sorted(MARKER_NAMES))}):\s*(.*?)\s*-->\s*$")

# The codes the log carries when the platform did not fill a marker, by what was asked, and the
# sentence each carries. Both sides of the seam write them — the session past its budget, the
# gateway when the embedder is down — so they are spelled here, once, and never phrased twice.
SKIPPED: dict[str, str] = {"memory": "memory_skipped", "retrieved": "retrieval_skipped"}
REMEMBER_FAILED = "remember_failed"
NOT_FILLED = "{what} was not filled: {why}"
NOT_REMEMBERED = "memory was not written: {why}"


@dataclass(frozen=True)
class Ask:
    """What a memory or retrieved marker asks for: which kinds, how many, how good."""

    kinds: tuple[str, ...] = ()
    limit: int | None = None
    min_score: float | None = None


@dataclass(frozen=True)
class Marker:
    """One marker as a view wrote it: who fills it, the payload it carries, and the whole line."""

    name: MarkerName
    # A knowledge marker's payload is the file's path, bare; memory's and retrieved's are JSON.
    payload: str
    line: str

    # `limit` and `k` are the same ask, as are `min_score` and `minScore`: TypeScript wrote the
    # camel case before ms-9 and writes snake case now, and the runtime reads both. A `fill` is a
    # render-prop id the app kept behind; this release the runtime renders with its own shape and
    # the id travels unread.
    @property
    def ask(self) -> Ask:
        """The payload of a memory or retrieved marker, as JSON; empty asks for the defaults."""
        if not self.payload:
            return Ask()
        said = _as_json(self.name, self.payload)
        limit = said.get("limit", said.get("k"))
        min_score = said.get("min_score", said.get("minScore"))
        return Ask(
            kinds=tuple(str(kind) for kind in said.get("kinds") or ()),
            limit=None if limit is None else int(limit),
            min_score=None if min_score is None else float(min_score),
        )


def markers_in(text: str) -> tuple[Marker, ...]:
    """Every marker line of a block, in the order it was written."""
    found = (_a_marker(line) for line in text.split("\n"))
    return tuple(marker for marker in found if marker is not None)


# A Markdown heading, as a view writes one over a marker: "## Lo que recordamos".
A_HEADING = re.compile(r"^\s*#{1,6}\s")


# A marker the fills leave out is filled with nothing: the model never reads a comment. An empty
# fill takes the line with it and one blank line after it, so the blank lines around a marker do
# not double up — and the heading the view wrote over it, when nothing else is under that
# heading: "## What you remember" with nothing remembered is a heading over silence, and the
# model would read it as a fact.
def filled(text: str, fills: Mapping[str, str]) -> str:
    """The block with every marker line replaced by its fill, keyed by the line as written."""
    if "<!--" not in text:
        return text
    lines: list[str | None] = list(text.split("\n"))
    emptied: list[int] = []
    for at, line in enumerate(lines):
        if line is None or A_MARKER.match(line) is None:
            continue
        fill = fills.get(line, "")
        if fill:
            lines[at] = fill
            continue
        lines[at] = None
        emptied.append(at)
        if at + 1 < len(lines) and _is_blank(lines[at + 1]):
            lines[at + 1] = None
    for at in emptied:
        _drop_the_heading_left_over(lines, at)
    return "\n".join(line for line in lines if line is not None)


def _drop_the_heading_left_over(lines: list[str | None], marker_at: int) -> None:
    """The heading right over an emptied marker goes too, unless something else is under it."""
    above = marker_at - 1
    while above >= 0 and _is_blank(lines[above]):
        above -= 1
    if above < 0 or A_HEADING.match(lines[above] or "") is None:
        return
    below = marker_at + 1
    while below < len(lines) and _is_blank(lines[below]):
        below += 1
    if below < len(lines) and A_HEADING.match(lines[below] or "") is None:
        return
    # A section at the very end takes the blank lines that led into it: nothing follows to need
    # them, and a block never ends in a blank line.
    first = above
    while below >= len(lines) and first > 0 and _is_blank(lines[first - 1]):
        first -= 1
    for at in range(first, marker_at):
        lines[at] = None


def _is_blank(line: str | None) -> bool:
    """A removed line or one with nothing on it: what a heading may sit over and still be alone."""
    return line is None or not line.strip()


def _a_marker(line: str) -> Marker | None:
    """The marker on this line, or None when the line is prose."""
    matched = A_MARKER.match(line)
    if matched is None:
        return None
    name: Any = matched.group(1)
    return Marker(name=name, payload=matched.group(2), line=line)


def _as_json(name: str, payload: str) -> dict[str, Any]:
    """The payload as the object it must be; anything else is the view's bug, named."""
    try:
        said: Any = json.loads(payload)
    except ValueError as bad:
        raise DeclarationRefused(f"a {name} marker carries JSON, not {payload!r}") from bad
    if not isinstance(said, dict):
        raise DeclarationRefused(f"a {name} marker carries a JSON object, not {payload!r}")
    return said  # pyright: ignore[reportUnknownVariableType] — json.loads is untyped

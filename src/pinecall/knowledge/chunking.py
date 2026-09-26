"""A Markdown file into the pieces retrieval hands a turn: by heading, under a cap, path kept."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from pinecall.types import KnowledgeFile
from pinecall.types.token_estimate import estimated_tokens

# The cap, in the tokens a model counts. A chunk that lands under 350 is small enough for eight of
# them in front of a turn and large enough to hold a whole tariff. What a token is worth is
# types/token_estimate.py's, the one estimate the contextual embedder windows a document by too.
CHUNK_TOKENS = 350

# Between the levels of a heading path ("Tarifas › Revisión"), and between a path and the text
# under it. Both indexes read the path with the text, so a search by a heading's words finds it.
HEADING_SEPARATOR = " › "
HEADING_JOINT = "\n\n"

# A heading a file is cut at: one to three hashes, then its text. Anything deeper is body.
A_HEADING = re.compile(r"^(#{1,3})\s+(.+?)\s*$")
# Where one paragraph ends and the next begins.
A_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
# Where one sentence ends, for the one paragraph that is over the cap by itself.
A_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")

# A file's front matter: a `---` line, the metadata, and the `---` line that closes it.
FENCE = "---\n"
A_CLOSING_FENCE = re.compile(r"^---[ \t]*$", re.MULTILINE)


@dataclass(frozen=True)
class Piece:
    """One chunk before it is embedded: its file, its heading path, its place, and its text."""

    path: str
    heading: str | None
    ordinal: int
    text: str


def chunks_of(file: KnowledgeFile) -> list[Piece]:
    """The file cut at its headings, every cut under the cap, in the order it was read."""
    pieces: list[Piece] = []
    for heading, body in _sections_of(without_front_matter(file.text)):
        for group in _paragraphs_under_the_cap(body, heading):
            pieces.append(Piece(file.path, heading, len(pieces), prefixed(heading, group)))
    return pieces


# Every static-site generator and every scraper opens a file with a fenced block of metadata —
# `source:`, `title:`, `scraped_at:` — and it is not prose: nobody asks a question it answers.
# Left in, it is the file's first section, so it is embedded, indexed and retrievable, and it
# wins a slot of the handful a turn gets. Measured on a scraped site: 75 of 537 chunks, one in
# seven, and one of them came back as evidence for a caller's phone number (2026-09-20).
def without_front_matter(text: str) -> str:
    """The file's text after a leading `---` block, or the text as it was when there is none."""
    if not text.startswith(FENCE):
        return text
    closed = A_CLOSING_FENCE.search(text, len(FENCE))
    return text[closed.end() :].lstrip("\n") if closed else text


def prefixed(heading: str | None, body: str) -> str:
    """The text a chunk is indexed as: its heading path over its body, or the body alone."""
    return f"{heading}{HEADING_JOINT}{body}" if heading else body


def body_of(text: str, heading: str | None) -> str:
    """The chunk's text without the heading path the indexes read: what the model reads."""
    return text.removeprefix(f"{heading}{HEADING_JOINT}") if heading else text


# A heading of level n closes every open heading of level n or deeper and opens under the rest,
# so "## Revisión" under "# Tarifas" is "Tarifas › Revisión" and the next "## Limpieza" is
# "Tarifas › Limpieza". What comes before the first heading is a section with no path.
def _sections_of(text: str) -> Iterator[tuple[str | None, str]]:
    """(heading path, body) for every stretch of the file under one heading, in order."""
    trail: list[tuple[int, str]] = []
    body: list[str] = []
    for line in text.split("\n"):
        heading = A_HEADING.match(line)
        if heading is None:
            body.append(line)
            continue
        yield _path_of(trail), "\n".join(body)
        body = []
        level = len(heading.group(1))
        trail = [*(kept for kept in trail if kept[0] < level), (level, heading.group(2))]
    yield _path_of(trail), "\n".join(body)


def _path_of(trail: Sequence[tuple[int, str]]) -> str | None:
    """The open headings, outermost first, as one path; None before any heading."""
    return HEADING_SEPARATOR.join(text for _level, text in trail) or None


def _paragraphs_under_the_cap(body: str, heading: str | None) -> list[str]:
    """The body's paragraphs grouped while they fit the cap, the heading's own words counted."""
    room = CHUNK_TOKENS - estimated_tokens(heading or "")
    paragraphs = [paragraph.strip() for paragraph in A_PARAGRAPH_BREAK.split(body)]
    fitting = [piece for paragraph in paragraphs if paragraph for piece in _within(paragraph, room)]
    return _grouped(fitting, HEADING_JOINT, room)


def _within(paragraph: str, room: int) -> list[str]:
    """The paragraph itself, or its sentences grouped under the room when it alone is over it."""
    if estimated_tokens(paragraph) <= room:
        return [paragraph]
    return _grouped(A_SENTENCE_END.split(paragraph), " ", room)


# A part longer than the room by itself stands alone, over it: the cap is where a cut is made,
# not a guillotine over a sentence that has no join to cut at.
def _grouped(parts: Sequence[str], joint: str, room: int) -> list[str]:
    """Consecutive parts joined while the join stays within the room, each group a text."""
    groups: list[str] = []
    current: list[str] = []
    for part in parts:
        if current and estimated_tokens(joint.join([*current, part])) > room:
            groups.append(joint.join(current))
            current = []
        current.append(part)
    if current:
        groups.append(joint.join(current))
    return groups

"""What the model reads: a source line per chunk, the heading once, a blank line between."""

import pytest

from pinecall.knowledge import chunks_as_text
from pinecall.types import Chunk

pytestmark = pytest.mark.unit


def a_chunk(path: str, heading: str | None, body: str) -> Chunk:
    """A chunk as search hands it back: the heading path over the body in its text."""
    text = f"{heading}\n\n{body}" if heading else body
    return Chunk(id="c1", base="clinica", path=path, heading=heading, text=text, score=1.0)


def test_each_chunk_is_its_source_line_then_its_body_and_the_heading_is_said_once() -> None:
    chunks = [
        a_chunk("tarifas.md", "Tarifas › Revisión", "La revisión cuesta cuarenta euros."),
        a_chunk("notas.md", None, "Somos la Clínica Norte."),
    ]
    assert chunks_as_text(chunks) == (
        "### tarifas.md › Tarifas › Revisión\n"
        "La revisión cuesta cuarenta euros.\n"
        "\n"
        "### notas.md\n"
        "Somos la Clínica Norte."
    )


def test_no_chunks_is_no_text() -> None:
    assert chunks_as_text([]) == ""

"""A file into pieces: the heading path over each, the cap held, a file with no headings too."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pinecall.knowledge.chunking import (
    A_HEADING as A_HEADING_LINE,
)
from pinecall.knowledge.chunking import (
    A_SENTENCE_END,
    CHUNK_TOKENS,
    HEADING_JOINT,
    body_of,
    chunks_of,
    prefixed,
)
from pinecall.types import KnowledgeFile
from pinecall.types.token_estimate import estimated_tokens

pytestmark = pytest.mark.unit

TARIFAS = KnowledgeFile(
    "tarifas.md",
    "# Tarifas\n\n## Revisión\n\nLa revisión cuesta cuarenta euros.\n\n"
    "### Con radiografía\n\nCon radiografía, cincuenta.\n\n"
    "## Limpieza\n\nLa limpieza dental cuesta sesenta euros.\n",
)


def test_every_piece_carries_the_path_of_the_headings_over_it() -> None:
    pieces = chunks_of(TARIFAS)
    assert [piece.heading for piece in pieces] == [
        "Tarifas › Revisión",
        "Tarifas › Revisión › Con radiografía",
        "Tarifas › Limpieza",
    ]
    assert [piece.ordinal for piece in pieces] == [0, 1, 2]
    assert all(piece.path == "tarifas.md" for piece in pieces)


def test_the_text_a_piece_is_indexed_as_opens_with_its_heading_path() -> None:
    first = chunks_of(TARIFAS)[0]
    assert first.text == "Tarifas › Revisión\n\nLa revisión cuesta cuarenta euros."
    assert body_of(first.text, first.heading) == "La revisión cuesta cuarenta euros."


def test_a_heading_with_nothing_under_it_is_no_piece_at_all() -> None:
    assert not any(piece.heading == "Tarifas" for piece in chunks_of(TARIFAS))


def test_what_comes_before_the_first_heading_is_a_piece_with_no_path() -> None:
    file = KnowledgeFile(
        "clinica.md", "Somos la Clínica Norte.\n\n# Horarios\n\nDe nueve a seis.\n"
    )
    assert [(piece.heading, piece.text) for piece in chunks_of(file)] == [
        (None, "Somos la Clínica Norte."),
        ("Horarios", "Horarios\n\nDe nueve a seis."),
    ]


def test_a_fourth_level_heading_is_body_and_never_a_cut() -> None:
    file = KnowledgeFile("faq.md", "# Preguntas\n\n#### Una fina\n\nSu respuesta.\n")
    [only] = chunks_of(file)
    assert only.heading == "Preguntas"
    assert only.text == "Preguntas\n\n#### Una fina\n\nSu respuesta."


def test_a_file_with_no_headings_is_read_by_paragraph_groups() -> None:
    paragraphs = [f"Párrafo {n} con unas cuantas palabras dentro." for n in range(4)]
    file = KnowledgeFile("notas.md", "\n\n".join(paragraphs))
    [only] = chunks_of(file)
    assert only.heading is None
    assert only.text == HEADING_JOINT.join(paragraphs)


def test_a_long_section_is_cut_at_its_paragraphs_and_every_cut_keeps_the_path() -> None:
    paragraph = " ".join(["palabra"] * 60)
    file = KnowledgeFile("largo.md", "# Guía\n\n## Todo\n\n" + "\n\n".join([paragraph] * 12))
    pieces = chunks_of(file)
    assert len(pieces) > 1
    assert all(piece.heading == "Guía › Todo" for piece in pieces)
    assert all(estimated_tokens(piece.text) <= CHUNK_TOKENS for piece in pieces)
    assert all(piece.text.startswith("Guía › Todo\n\npalabra") for piece in pieces)
    assert [piece.ordinal for piece in pieces] == list(range(len(pieces)))


def test_one_paragraph_over_the_cap_is_cut_at_its_sentences() -> None:
    sentence = " ".join(["palabra"] * 40) + "."
    file = KnowledgeFile("denso.md", " ".join([sentence] * 10))
    pieces = chunks_of(file)
    assert len(pieces) > 1
    assert all(estimated_tokens(piece.text) <= CHUNK_TOKENS for piece in pieces)
    assert all(piece.text.endswith(".") for piece in pieces)


def test_the_estimate_is_the_words_times_one_point_three() -> None:
    assert estimated_tokens("diez palabras aquí para contar y ver la cuenta hecha") == 13
    assert estimated_tokens("") == 0


def test_an_empty_file_is_no_piece() -> None:
    assert chunks_of(KnowledgeFile("vacio.md", "")) == []
    assert chunks_of(KnowledgeFile("solo.md", "# Solo un título\n")) == []


def test_prefixed_and_body_of_are_each_others_inverse() -> None:
    assert body_of(prefixed("Tarifas", "cuarenta"), "Tarifas") == "cuarenta"
    assert body_of(prefixed(None, "cuarenta"), None) == "cuarenta"


# A scraper and every static-site generator open a file with a fenced block of metadata. Left in,
# it is the file's first section: embedded, indexed, retrievable — 75 of a real site's 537 chunks,
# one in seven, and one came back as the evidence for a caller's phone number (2026-09-20).
def test_a_files_front_matter_is_not_a_chunk() -> None:
    file = KnowledgeFile(
        "about.md",
        "---\nsource: https://example.com/about\n"
        'title: "Who we are"\nscraped_at: 2026-08-22\n---\n\n'
        "## What this page answers\n\nWe clean offices.",
    )

    pieces = chunks_of(file)

    assert len(pieces) == 1
    assert "scraped_at" not in pieces[0].text
    assert pieces[0].heading == "What this page answers"


def test_a_file_that_opens_with_a_rule_and_never_closes_it_is_left_alone() -> None:
    """`---` is also a horizontal rule: what is not a CLOSED block is somebody's own text."""
    file = KnowledgeFile("rule.md", "---\n\n## Tarifas\n\ncuarenta euros")

    assert [piece.heading for piece in chunks_of(file)] == [None, "Tarifas"]
    assert chunks_of(file)[0].text == "---"


def test_the_front_matter_of_a_file_with_no_headings_is_still_dropped() -> None:
    file = KnowledgeFile("flat.md", "---\ntitle: x\n---\n\nWe clean offices.")

    pieces = chunks_of(file)

    assert [piece.text for piece in pieces] == ["We clean offices."]


# What any file at all must come out as, searched for rather than listed: hypothesis writes the
# files, from headings and paragraphs of words it invents.
A_WORD = st.text(alphabet=st.characters(categories=("L", "N")), min_size=1, max_size=12)
A_PARAGRAPH = st.lists(A_WORD, min_size=1, max_size=120).map(" ".join)
A_HEADING = st.tuples(st.integers(1, 3), A_WORD).map(lambda h: f"{'#' * h[0]} {h[1]}")
A_FILE = (
    st.lists(st.one_of(A_HEADING, A_PARAGRAPH), max_size=12)
    .map("\n\n".join)
    .map(lambda text: KnowledgeFile("any.md", text))
)


@given(file=A_FILE)
def test_every_piece_is_under_the_cap_or_is_one_sentence_that_could_not_be_cut(
    file: KnowledgeFile,
) -> None:
    for piece in chunks_of(file):
        body = body_of(piece.text, piece.heading)
        assert estimated_tokens(piece.text) <= CHUNK_TOKENS or len(A_SENTENCE_END.split(body)) == 1


@given(file=A_FILE)
def test_the_pieces_are_numbered_from_zero_carry_the_path_and_lose_no_word(
    file: KnowledgeFile,
) -> None:
    pieces = chunks_of(file)
    assert [piece.ordinal for piece in pieces] == list(range(len(pieces)))
    assert all(piece.path == file.path for piece in pieces)
    said = [word for piece in pieces for word in body_of(piece.text, piece.heading).split()]
    prose = [line for line in file.text.split("\n") if not A_HEADING_LINE.match(line)]
    assert said == [word for line in prose for word in line.split()]

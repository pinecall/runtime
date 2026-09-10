"""A file into pieces: the heading path over each, the cap held, a file with no headings too."""

import pytest

from pinecall.knowledge.chunking import (
    CHUNK_TOKENS,
    HEADING_JOINT,
    body_of,
    chunks_of,
    prefixed,
)
from pinecall.types import KnowledgeFile
from pinecall.types.counting import estimated_tokens

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

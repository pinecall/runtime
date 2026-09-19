"""A file pushed whole is one row, uncut and never embedded: the static block's, not a search's."""

from __future__ import annotations

import pytest

from pinecall.knowledge import PgKnowledge
from pinecall.knowledge.chunking import chunks_of
from pinecall.types import PRODUCTION, DeclarationRefused, KnowledgeFile
from tests.knowledge.files import CLINICA, TARIFAS
from tests.knowledge.test_the_push import RecordingEmbedder, RecordingPool

pytestmark = pytest.mark.unit

BY_HEART = KnowledgeFile(
    "maravilla.md", "# Maravilla\n\nUn taller.\n\n## Horarios\n\nDe nueve a seis.\n", "whole"
)


def test_a_whole_file_is_one_piece_with_its_text_as_it_came() -> None:
    [piece] = chunks_of(BY_HEART)
    assert (piece.path, piece.heading, piece.ordinal, piece.text) == (
        "maravilla.md",
        None,
        0,
        BY_HEART.text,
    )
    assert len(chunks_of(KnowledgeFile("maravilla.md", BY_HEART.text))) == 2


def test_a_mode_the_runtime_has_no_word_for_is_refused() -> None:
    with pytest.raises(DeclarationRefused, match="one of \\['retrieved', 'whole'\\]"):
        KnowledgeFile("maravilla.md", "x", "verbatim")  # type: ignore[arg-type]


async def test_a_push_embeds_the_cut_files_only_and_writes_the_whole_one_with_no_vector() -> None:
    embedder = RecordingEmbedder()
    pool = RecordingPool()
    await PgKnowledge(pool, embedder).put(
        "org", PRODUCTION, None, "clinica", [CLINICA, BY_HEART, TARIFAS]
    )
    # Two documents reached the embedder, and neither is the file kept whole.
    assert [len(document) for document in embedder.documents] == [2, 2]
    assert not any(BY_HEART.text in text for document in embedder.documents for text in document)
    paths, _headings, _ordinals, _texts, vectors, modes = pool.arguments[7:]
    assert paths == ["clinica.md", "clinica.md", "maravilla.md", "tarifas.md", "tarifas.md"]
    assert modes == ["retrieved", "retrieved", "whole", "retrieved", "retrieved"]
    assert [vector is None for vector in vectors] == [False, False, True, False, False]

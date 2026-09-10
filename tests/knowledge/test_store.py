"""The base in Postgres: a push replaces it, a search reads it by words and by meaning, fused."""

from collections.abc import AsyncIterator
from typing import Any, override

import pytest

from pinecall.knowledge import Base, PgKnowledge
from pinecall.log.store import open_pool
from pinecall.providers.embedder import DIMENSIONS, WrongModel
from tests.knowledge.files import CLINICA, TARIFAS, an_org
from tests.postgres import Dev
from tests.vectors import HASH_MODEL, HashEmbedder

pytestmark = pytest.mark.postgres

THE_BASE = "clinica"


@pytest.fixture
async def knowledge(postgres: Dev) -> AsyncIterator[PgKnowledge]:
    """The store on this run's schema, with the embedder that needs no TEI."""
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        yield PgKnowledge(pool, HashEmbedder())
    finally:
        await pool.close()


@pytest.fixture
async def org(raw_connection: Any) -> str:
    """An org of this test's own, in the table the base's row references."""
    return await an_org(raw_connection)


async def test_a_push_counts_its_chunks_and_a_second_push_replaces_the_first(
    knowledge: PgKnowledge, org: str
) -> None:
    assert await knowledge.put(org, THE_BASE, [CLINICA, TARIFAS]) == 4
    [listed] = await knowledge.bases(org)
    assert (listed.base, listed.chunks) == (THE_BASE, 4)
    assert await knowledge.put(org, THE_BASE, [TARIFAS]) == 2
    [listed] = await knowledge.bases(org)
    assert (listed.base, listed.chunks) == (THE_BASE, 2)
    found = await knowledge.search(org, THE_BASE, "horarios turnos")
    assert {chunk.path for chunk in found} == {"tarifas.md"}


async def test_the_base_row_says_which_model_wrote_the_vectors_and_at_which_width(
    knowledge: PgKnowledge, org: str, raw_connection: Any
) -> None:
    await knowledge.put(org, THE_BASE, [CLINICA])
    row = await raw_connection.fetchrow(
        "select model, dimensions, pushed_at from knowledge_bases where org = $1", org
    )
    assert (row["model"], row["dimensions"]) == (HASH_MODEL, DIMENSIONS)
    assert isinstance((await knowledge.bases(org))[0], Base)


async def test_a_search_finds_a_chunk_by_a_heading_word_the_text_search_stems(
    knowledge: PgKnowledge, org: str
) -> None:
    """'turno' is not a word of the chunk; 'Turnos' is, and spanish stems them to one. Only BM25
    can: the hash embedder points 'turno' nowhere near 'turnos'."""
    await knowledge.put(org, THE_BASE, [CLINICA, TARIFAS])
    first, *_rest = await knowledge.search(org, THE_BASE, "turno")
    assert first.heading == "Clínica Norte › Turnos"
    assert first.text.startswith("Clínica Norte › Turnos\n\nLos turnos se piden")


async def test_a_search_finds_a_chunk_by_words_the_text_search_drops_because_the_vector_keeps_them(
    knowledge: PgKnowledge, org: str
) -> None:
    """'por', 'con' and 'el' are stopwords to spanish and BM25 finds nothing; they are words to the
    hash embedder, and only the Turnos chunk has all three."""
    await knowledge.put(org, THE_BASE, [CLINICA, TARIFAS])
    first, *_rest = await knowledge.search(org, THE_BASE, "por con el")
    assert first.heading == "Clínica Norte › Turnos"


async def test_a_chunk_both_branches_find_scores_one_and_min_score_drops_the_rest(
    knowledge: PgKnowledge, org: str
) -> None:
    await knowledge.put(org, THE_BASE, [CLINICA, TARIFAS])
    found = await knowledge.search(org, THE_BASE, "turnos teléfono")
    assert [chunk.heading for chunk in found][0] == "Clínica Norte › Turnos"
    assert found[0].score == 1.0
    assert all(chunk.score < 0.6 for chunk in found[1:])
    assert len(found) == 4
    kept = await knowledge.search(org, THE_BASE, "turnos teléfono", min_score=0.6)
    assert [chunk.heading for chunk in kept] == ["Clínica Norte › Turnos"]


async def test_k_caps_what_a_turn_is_handed(knowledge: PgKnowledge, org: str) -> None:
    await knowledge.put(org, THE_BASE, [CLINICA, TARIFAS])
    assert len(await knowledge.search(org, THE_BASE, "revisión", k=1)) == 1
    assert len(await knowledge.search(org, THE_BASE, "revisión", k=3)) == 3


async def test_dropping_a_base_never_pushed_answers_false_and_a_pushed_one_true(
    knowledge: PgKnowledge, org: str
) -> None:
    assert await knowledge.drop(org, "nunca") is False
    await knowledge.put(org, THE_BASE, [CLINICA])
    assert await knowledge.drop(org, THE_BASE) is True
    assert await knowledge.bases(org) == []
    assert await knowledge.search(org, THE_BASE, "horarios") == []
    assert await knowledge.drop(org, THE_BASE) is False


async def test_an_org_never_sees_another_orgs_base_of_the_same_name(
    knowledge: PgKnowledge, org: str, raw_connection: Any
) -> None:
    other = await an_org(raw_connection)
    await knowledge.put(org, THE_BASE, [CLINICA])
    await knowledge.put(other, THE_BASE, [TARIFAS])
    assert {chunk.path for chunk in await knowledge.search(org, THE_BASE, "revisión")} == {
        "clinica.md"
    }
    assert [listed.chunks for listed in await knowledge.bases(other)] == [2]


async def test_a_push_of_nothing_is_a_base_with_no_chunks(knowledge: PgKnowledge, org: str) -> None:
    assert await knowledge.put(org, THE_BASE, []) == 0
    assert [listed.chunks for listed in await knowledge.bases(org)] == [0]
    assert await knowledge.search(org, THE_BASE, "horarios") == []


async def test_a_base_pushed_with_another_model_is_refused_naming_both_and_the_way_out(
    knowledge: PgKnowledge, org: str, postgres: Dev
) -> None:
    """Two models' vectors are numbers of the same width; ranking one by the other is a plausible
    answer with no meaning in it. So the search refuses, and the sentence says to push again."""
    await knowledge.put(org, THE_BASE, [CLINICA])
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        other = PgKnowledge(pool, OtherModel())
        with pytest.raises(WrongModel) as refused:
            await other.search(org, THE_BASE, "horarios")
    finally:
        await pool.close()
    assert str(refused.value) == (
        f"base {THE_BASE} was pushed with {HASH_MODEL}; "
        f"this gateway embeds with another-embedder: push it again"
    )


async def test_a_base_nobody_pushed_is_not_a_model_mismatch_but_an_empty_answer(
    org: str, postgres: Dev
) -> None:
    """There is nothing to compare against, and a search of a name nobody pushed always said so."""
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        assert await PgKnowledge(pool, OtherModel()).search(org, "nunca", "horarios") == []
    finally:
        await pool.close()


class OtherModel(HashEmbedder):
    """The same vectors under another model's name: only the name is what a base is refused by."""

    @override
    async def model(self) -> str:
        return "another-embedder"


async def test_the_chunks_an_org_keeps_are_summed_over_its_bases_and_one_can_be_left_out(
    knowledge: PgKnowledge, org: str
) -> None:
    """What knowledge_chunks is measured against: a sum over the base rows, never a table."""
    assert await knowledge.kept(org) == 0
    await knowledge.put(org, THE_BASE, [CLINICA, TARIFAS])
    await knowledge.put(org, "tarifas", [TARIFAS])
    assert await knowledge.kept(org) == 6
    assert await knowledge.kept(org, besides=THE_BASE) == 2, "the base a push replaces is freed"
    assert await knowledge.kept(org, besides="nadie") == 6, "a base nobody pushed frees nothing"
    await knowledge.drop(org, "tarifas")
    assert await knowledge.kept(org) == 4


async def test_how_many_chunks_a_push_would_become_is_the_cut_the_push_itself_makes(
    knowledge: PgKnowledge, org: str
) -> None:
    """The number the quota judges a push by has to be the number the push then writes."""
    assert knowledge.how_many_chunks([CLINICA, TARIFAS]) == 4
    assert knowledge.how_many_chunks([]) == 0
    assert await knowledge.put(org, THE_BASE, [CLINICA, TARIFAS]) == 4

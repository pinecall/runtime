"""The base in Postgres: a push replaces it, a search reads it by words and by meaning, fused."""

from collections.abc import AsyncIterator
from typing import Any, override

import pytest

from pinecall.db import open_pool
from pinecall.knowledge import Base, PgKnowledge
from pinecall.providers.embedder import DIMENSIONS, WrongModel
from pinecall.types import PRODUCTION, SANDBOX, KnowledgeFile
from tests.knowledge.files import CLINICA, TARIFAS, VENDING, an_org
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
    assert await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA, TARIFAS]) == 4
    [listed] = await knowledge.bases(org, PRODUCTION)
    assert (listed.base, listed.chunks) == (THE_BASE, 4)
    assert await knowledge.put(org, PRODUCTION, None, THE_BASE, [TARIFAS]) == 2
    [listed] = await knowledge.bases(org, PRODUCTION)
    assert (listed.base, listed.chunks) == (THE_BASE, 2)
    found = await knowledge.search(org, PRODUCTION, None, [THE_BASE], "horarios turnos")
    assert {chunk.path for chunk in found} == {"tarifas.md"}


async def test_a_push_keeps_its_files_and_a_file_is_put_and_taken_out_alone(
    knowledge: PgKnowledge, org: str
) -> None:
    """The files are what a person reads and edits; the chunks stay the index, kept in step."""
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA, TARIFAS])
    listed = await knowledge.files(org, PRODUCTION, None, THE_BASE)
    assert [(one.path, one.chunks, one.chars) for one in listed] == [
        ("clinica.md", 2, len(CLINICA.text)),
        ("tarifas.md", 2, len(TARIFAS.text)),
    ]
    read = await knowledge.file(org, PRODUCTION, None, THE_BASE, "tarifas.md")
    assert read is not None and read.text == TARIFAS.text

    # One file put alone: its chunks replaced, the others untouched, the base's count moved.
    horarios = KnowledgeFile("faq/horarios.md", "# Horarios\n\n## Semana\n\nDe nueve a veinte.\n")
    assert await knowledge.put_file(org, PRODUCTION, None, THE_BASE, horarios) == 1
    [base] = await knowledge.bases(org, PRODUCTION)
    assert base.chunks == 5
    found = await knowledge.search(org, PRODUCTION, None, [THE_BASE], "nueve veinte semana")
    assert found[0].path == "faq/horarios.md"

    shorter = KnowledgeFile("tarifas.md", "# Tarifas\n\nTodo cuesta cuarenta euros.\n")
    assert await knowledge.put_file(org, PRODUCTION, None, THE_BASE, shorter) == 1
    [base] = await knowledge.bases(org, PRODUCTION)
    assert base.chunks == 4
    assert await knowledge.freed_by(org, PRODUCTION, None, THE_BASE, "tarifas.md") == 1

    assert await knowledge.drop_file(org, PRODUCTION, None, THE_BASE, "tarifas.md") is True
    assert await knowledge.drop_file(org, PRODUCTION, None, THE_BASE, "tarifas.md") is False
    [base] = await knowledge.bases(org, PRODUCTION)
    assert base.chunks == 3
    assert [one.path for one in await knowledge.files(org, PRODUCTION, None, THE_BASE)] == [
        "clinica.md",
        "faq/horarios.md",
    ]


async def test_a_file_begins_a_base_and_the_last_file_out_takes_the_base_with_it(
    knowledge: PgKnowledge, org: str
) -> None:
    assert await knowledge.put_file(org, SANDBOX, None, "nueva", TARIFAS) == 2
    [base] = await knowledge.bases(org, SANDBOX)
    assert (base.base, base.chunks) == ("nueva", 2)
    assert await knowledge.drop_file(org, SANDBOX, None, "nueva", "tarifas.md") is True
    assert await knowledge.bases(org, SANDBOX) == []


async def test_a_second_push_forgets_the_files_the_folder_no_longer_has(
    knowledge: PgKnowledge, org: str
) -> None:
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA, TARIFAS])
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [TARIFAS])
    assert [one.path for one in await knowledge.files(org, PRODUCTION, None, THE_BASE)] == [
        "tarifas.md"
    ]


async def test_the_base_row_says_which_model_wrote_the_vectors_and_at_which_width(
    knowledge: PgKnowledge, org: str, raw_connection: Any
) -> None:
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA])
    row = await raw_connection.fetchrow(
        "select model, dimensions, pushed_at from knowledge_bases where org = $1", org
    )
    assert (row["model"], row["dimensions"]) == (HASH_MODEL, DIMENSIONS)
    assert isinstance((await knowledge.bases(org, PRODUCTION))[0], Base)


async def test_a_search_finds_a_chunk_by_a_heading_word_the_text_search_stems(
    knowledge: PgKnowledge, org: str
) -> None:
    """'turno' is not a word of the chunk; 'Turnos' is, and spanish stems them to one. Only BM25
    can: the hash embedder points 'turno' nowhere near 'turnos'."""
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA, TARIFAS])
    first, *_rest = await knowledge.search(org, PRODUCTION, None, [THE_BASE], "turno")
    assert first.heading == "Clínica Norte › Turnos"
    assert first.text.startswith("Clínica Norte › Turnos\n\nLos turnos se piden")


async def test_a_search_finds_a_chunk_by_words_the_text_search_drops_because_the_vector_keeps_them(
    knowledge: PgKnowledge, org: str
) -> None:
    """'por', 'con' and 'el' are stopwords to spanish and BM25 finds nothing; they are words to the
    hash embedder, and only the Turnos chunk has all three."""
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA, TARIFAS])
    first, *_rest = await knowledge.search(org, PRODUCTION, None, [THE_BASE], "por con el")
    assert first.heading == "Clínica Norte › Turnos"


async def test_a_chunk_both_branches_find_scores_one_and_min_score_drops_the_rest(
    knowledge: PgKnowledge, org: str
) -> None:
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA, TARIFAS])
    found = await knowledge.search(org, PRODUCTION, None, [THE_BASE], "turnos teléfono")
    assert next(chunk.heading for chunk in found) == "Clínica Norte › Turnos"
    assert found[0].score == 1.0
    assert all(chunk.score < 0.6 for chunk in found[1:])
    assert len(found) == 4
    kept = await knowledge.search(
        org, PRODUCTION, None, [THE_BASE], "turnos teléfono", floors={THE_BASE: 0.6}
    )
    assert [chunk.heading for chunk in kept] == ["Clínica Norte › Turnos"]


async def test_k_caps_what_a_turn_is_handed(knowledge: PgKnowledge, org: str) -> None:
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA, TARIFAS])
    assert len(await knowledge.search(org, PRODUCTION, None, [THE_BASE], "revisión", k=1)) == 1
    assert len(await knowledge.search(org, PRODUCTION, None, [THE_BASE], "revisión", k=3)) == 3


async def test_dropping_a_base_never_pushed_answers_false_and_a_pushed_one_true(
    knowledge: PgKnowledge, org: str
) -> None:
    assert await knowledge.drop(org, PRODUCTION, None, "nunca") is False
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA])
    assert await knowledge.drop(org, PRODUCTION, None, THE_BASE) is True
    assert await knowledge.bases(org, PRODUCTION) == []
    assert await knowledge.search(org, PRODUCTION, None, [THE_BASE], "horarios") == []
    assert await knowledge.drop(org, PRODUCTION, None, THE_BASE) is False


async def test_an_org_never_sees_another_orgs_base_of_the_same_name(
    knowledge: PgKnowledge, org: str, raw_connection: Any
) -> None:
    other = await an_org(raw_connection)
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA])
    await knowledge.put(other, PRODUCTION, None, THE_BASE, [TARIFAS])
    assert {
        chunk.path
        for chunk in await knowledge.search(org, PRODUCTION, None, [THE_BASE], "revisión")
    } == {"clinica.md"}
    assert [listed.chunks for listed in await knowledge.bases(other, PRODUCTION)] == [2]


async def test_a_push_of_nothing_is_a_base_with_no_chunks(knowledge: PgKnowledge, org: str) -> None:
    assert await knowledge.put(org, PRODUCTION, None, THE_BASE, []) == 0
    assert [listed.chunks for listed in await knowledge.bases(org, PRODUCTION)] == [0]
    assert await knowledge.search(org, PRODUCTION, None, [THE_BASE], "horarios") == []


async def test_a_base_pushed_with_another_model_is_refused_naming_both_and_the_way_out(
    knowledge: PgKnowledge, org: str, postgres: Dev
) -> None:
    """Two models' vectors are numbers of the same width; ranking one by the other is a plausible
    answer with no meaning in it. So the search refuses, and the sentence says to push again."""
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA])
    pool = await open_pool(postgres.dsn, schema=postgres.schema)
    try:
        other = PgKnowledge(pool, OtherModel())
        with pytest.raises(WrongModel) as refused:
            await other.search(org, PRODUCTION, None, [THE_BASE], "horarios")
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
        assert (
            await PgKnowledge(pool, OtherModel()).search(
                org, PRODUCTION, None, ["nunca"], "horarios"
            )
            == []
        )
    finally:
        await pool.close()


class OtherModel(HashEmbedder):
    """The same vectors under another model's name: only the name is what a base is refused by."""

    @override
    async def model(self) -> str:
        return "another-embedder"


async def test_the_chunks_an_org_keeps_are_summed_over_its_bases_in_both_worlds(
    knowledge: PgKnowledge, org: str
) -> None:
    """What knowledge_chunks is measured against: a sum over the base rows, never a table — and
    a laptop's base is rows on the same disk as the box's, so both worlds are in the sum."""
    assert await knowledge.kept(org) == 0
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA, TARIFAS])
    await knowledge.put(org, SANDBOX, None, THE_BASE, [TARIFAS])
    assert await knowledge.kept(org) == 6
    await knowledge.drop(org, SANDBOX, None, THE_BASE)
    assert await knowledge.kept(org) == 4


async def test_a_base_is_one_worlds_and_a_laptops_push_never_touches_the_boxs(
    knowledge: PgKnowledge, org: str
) -> None:
    """The point of 0018: one name in both worlds is two bases, and a search reads one of them."""
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA])
    await knowledge.put(org, SANDBOX, None, THE_BASE, [TARIFAS])
    deployed = {
        chunk.path for chunk in await knowledge.search(org, PRODUCTION, None, [THE_BASE], "turnos")
    }
    written = {
        chunk.path for chunk in await knowledge.search(org, SANDBOX, None, [THE_BASE], "turnos")
    }
    assert deployed == {CLINICA.path}
    assert written == {TARIFAS.path}
    assert [one.chunks for one in await knowledge.bases(org, PRODUCTION)] == [2]
    assert [one.chunks for one in await knowledge.bases(org, SANDBOX)] == [2]
    # Dropping the laptop's leaves the telephone's answering exactly as before.
    assert await knowledge.drop(org, SANDBOX, None, THE_BASE) is True
    assert await knowledge.search(org, SANDBOX, None, [THE_BASE], "turnos") == []
    assert len(await knowledge.search(org, PRODUCTION, None, [THE_BASE], "turnos")) == 2


async def test_how_many_chunks_a_push_would_become_is_the_cut_the_push_itself_makes(
    knowledge: PgKnowledge, org: str
) -> None:
    """The number the quota judges a push by has to be the number the push then writes."""
    assert knowledge.how_many_chunks([CLINICA, TARIFAS]) == 4
    assert knowledge.how_many_chunks([]) == 0
    assert await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA, TARIFAS]) == 4


# ── one developer's own ─────────────────────────────────────────────────────────

ANA = "m_ana"
BETO = "m_beto"


async def test_two_developers_push_their_own_and_neither_replaces_the_others(
    knowledge: PgKnowledge, org: str
) -> None:
    """The point of 0021: before it, the second push replaced what the first was testing against."""
    await knowledge.put(org, SANDBOX, ANA, THE_BASE, [CLINICA])
    await knowledge.put(org, SANDBOX, BETO, THE_BASE, [TARIFAS])

    anas = await knowledge.search(org, SANDBOX, ANA, [THE_BASE], "turnos")
    betos = await knowledge.search(org, SANDBOX, BETO, [THE_BASE], "turnos")

    assert {chunk.path for chunk in anas} == {CLINICA.path}
    assert {chunk.path for chunk in betos} == {TARIFAS.path}


async def test_a_developer_who_has_pushed_nothing_reads_the_orgs_own(
    knowledge: PgKnowledge, org: str
) -> None:
    """Nobody joins a team to an empty knowledge base: a READ falls back the way `of()` does."""
    await knowledge.put(org, SANDBOX, None, THE_BASE, [CLINICA])

    found = await knowledge.search(org, SANDBOX, ANA, [THE_BASE], "turnos")

    assert {chunk.path for chunk in found} == {CLINICA.path}
    assert [one.base for one in await knowledge.bases(org, SANDBOX, ANA)] == [THE_BASE]


async def test_their_own_wins_over_the_orgs_own_once_they_have_pushed(
    knowledge: PgKnowledge, org: str
) -> None:
    await knowledge.put(org, SANDBOX, None, THE_BASE, [CLINICA])
    await knowledge.put(org, SANDBOX, ANA, THE_BASE, [TARIFAS])

    found = await knowledge.search(org, SANDBOX, ANA, [THE_BASE], "turnos")

    assert {chunk.path for chunk in found} == {TARIFAS.path}
    assert [one.chunks for one in await knowledge.bases(org, SANDBOX, ANA)] == [2]


async def test_a_drop_takes_your_own_copy_and_never_the_orgs(
    knowledge: PgKnowledge, org: str
) -> None:
    """A `knowledge drop` on a laptop must not take the base the team — or the telephone — reads."""
    await knowledge.put(org, SANDBOX, None, THE_BASE, [CLINICA])
    await knowledge.put(org, SANDBOX, ANA, THE_BASE, [TARIFAS])

    assert await knowledge.drop(org, SANDBOX, ANA, THE_BASE) is True

    # Hers is gone, and she reads the org's again rather than nothing.
    found = await knowledge.search(org, SANDBOX, ANA, [THE_BASE], "turnos")
    assert {chunk.path for chunk in found} == {CLINICA.path}


async def test_dropping_a_name_you_never_pushed_says_so_even_where_the_org_has_one(
    knowledge: PgKnowledge, org: str
) -> None:
    await knowledge.put(org, SANDBOX, None, THE_BASE, [CLINICA])

    assert await knowledge.drop(org, SANDBOX, ANA, THE_BASE) is False
    assert len(await knowledge.search(org, SANDBOX, None, [THE_BASE], "turnos")) == 2


async def test_the_quota_counts_every_corner_because_the_rows_are_the_orgs(
    knowledge: PgKnowledge, org: str
) -> None:
    """Two developers each holding a base is two bases against the plan: one disk, one bill."""
    await knowledge.put(org, SANDBOX, ANA, THE_BASE, [CLINICA])
    await knowledge.put(org, SANDBOX, BETO, THE_BASE, [TARIFAS])

    assert await knowledge.kept(org) == 4


async def test_a_second_base_with_nothing_relevant_in_it_takes_none_of_the_turns_slots(
    knowledge: PgKnowledge, org: str
) -> None:
    """The whole point of searching the union: a score is only worth what it was ranked against.
    Searched one base at a time and merged afterwards, the vending base's best chunk came back at
    1.0 — the fusion reads relative to the best of ITS query — and took a slot from the clinic's
    own answer before the ranking had said anything."""
    await knowledge.put(org, PRODUCTION, None, THE_BASE, [CLINICA, TARIFAS])
    await knowledge.put(org, PRODUCTION, None, "vending", [VENDING])

    found = await knowledge.search(org, PRODUCTION, None, [THE_BASE, "vending"], "turnos teléfono")
    assert [chunk.base for chunk in found[:2]] == [THE_BASE, THE_BASE]
    assert found[0].heading == "Clínica Norte › Turnos"
    assert found[0].score == 1.0
    assert all(chunk.score < 1.0 for chunk in found if chunk.base == "vending")


async def test_one_base_handed_as_a_string_is_refused_and_not_read_letter_by_letter(
    knowledge: PgKnowledge, org: str
) -> None:
    """`str` is a Sequence[str]: a caller that hands one base the way the old signature took it
    would ask for its letters and be answered nothing at all."""
    with pytest.raises(TypeError, match="as a list"):
        await knowledge.search(org, PRODUCTION, None, THE_BASE, "turnos")  # type: ignore[arg-type]

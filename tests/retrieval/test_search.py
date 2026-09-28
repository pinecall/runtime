"""The hybrid search: two branches in one statement, fused by rank, over the rows a scope sees."""

import math

import pytest
from psycopg import sql

from pinecall.postgres.pool import Pool
from pinecall.retrieval.embed import halfvec
from pinecall.retrieval.search import (
    CANDIDATES_PER_BRANCH,
    RRF_K,
    Hit,
    SearchedTable,
    evidence_of,
    hybrid,
    relative_to_the_best,
    top_cosine,
)
from pinecall.tenancy import orgs
from tests.conftest import postgres

BASE = "clinica-norte"
INSERT_BASE = """
INSERT INTO knowledge_bases (org, env, holder, base, model, dimensions, chunks)
VALUES (%(org)s, 'production', '', %(base)s, 'embed-context-4b', 1024, 0)
"""
INSERT_CHUNK = """
INSERT INTO knowledge_chunks (org, env, holder, base, path, heading, ordinal, text, embedding)
VALUES (%(org)s, 'production', '', %(base)s, %(path)s, NULL, 0, %(text)s, %(embedding)s::halfvec)
"""
SCOPE = sql.SQL("org = %(org)s AND env = 'production' AND holder = '' AND base = %(base)s")


def toward(*weights: float) -> list[float]:
    """A unit vector of 1024 whose first values are the weights given."""
    length = math.sqrt(sum(weight * weight for weight in weights))
    return [weight / length for weight in weights] + [0.0] * (1024 - len(weights))


def chunks_of(org: str, base: str = BASE) -> SearchedTable:
    """The knowledge chunks of one base, a hit carrying its path and text."""
    return SearchedTable(
        name="knowledge_chunks",
        text_index="knowledge_chunks_text_bm25",
        scope=SCOPE,
        params={"org": org, "base": base},
        columns=("path", "text"),
    )


@pytest.fixture
async def org(pool: Pool) -> str:
    """An org with one empty base."""
    created = await orgs.create(pool, "clinica-norte", "Clinica Norte")
    async with pool.connection() as connection:
        await connection.execute(INSERT_BASE, {"org": created.id, "base": BASE})
    return created.id


async def kept(pool: Pool, org: str, path: str, text: str, vector: list[float]) -> None:
    """A chunk of the base, as a push would have written it."""
    row = {"org": org, "base": BASE, "path": path, "text": text, "embedding": halfvec(vector)}
    async with pool.connection() as connection:
        await connection.execute(INSERT_CHUNK, row)


async def found(
    pool: Pool, org: str, *, vector: list[float], words: str, room: int = CANDIDATES_PER_BRANCH
) -> list[Hit]:
    async with pool.connection() as connection:
        return await hybrid(connection, chunks_of(org), vector=vector, words=words, room=room)


def paths(hits: list[Hit]) -> list[str]:
    return [hit.row["path"] for hit in hits]


@postgres
async def test_a_row_is_found_by_its_words_where_its_vector_says_nothing(
    pool: Pool, org: str
) -> None:
    await kept(pool, org, "horario.md", "Abrimos de lunes a viernes", toward(1, 0))
    await kept(pool, org, "tarifas.md", "La revisión anual cuesta cuarenta euros", toward(0, 1))
    hits = await found(pool, org, vector=toward(1, 0), words="revisión", room=1)
    assert sorted(paths(hits)) == ["horario.md", "tarifas.md"]
    by_words = next(hit for hit in hits if hit.row["path"] == "tarifas.md")
    assert by_words.cosine == pytest.approx(0.0, abs=1e-3)


@postgres
async def test_a_row_is_found_by_its_vector_where_its_words_say_nothing(
    pool: Pool, org: str
) -> None:
    await kept(pool, org, "horario.md", "Abrimos de lunes a viernes", toward(1, 0))
    await kept(pool, org, "tarifas.md", "La revisión anual cuesta cuarenta euros", toward(0, 1))
    hits = await found(pool, org, vector=toward(0, 1), words="aparcamiento", room=1)
    assert paths(hits) == ["tarifas.md"]
    assert hits[0].cosine == pytest.approx(1.0, abs=1e-3)


@postgres
async def test_the_row_both_branches_find_comes_before_the_first_of_either_alone(
    pool: Pool, org: str
) -> None:
    await kept(pool, org, "cerca.md", "Abrimos de lunes a viernes", toward(1, 0))
    await kept(pool, org, "ambos.md", "La revisión se hace en el taller", toward(1, 0.2))
    await kept(pool, org, "palabras.md", "Revisión, revisión y más revisión", toward(0, 1))
    hits = await found(pool, org, vector=toward(1, 0), words="revisión", room=2)
    assert paths(hits)[0] == "ambos.md"


@postgres
async def test_a_rank_weighs_one_over_sixty_plus_it_and_both_branches_add_up(
    pool: Pool, org: str
) -> None:
    await kept(pool, org, "ambos.md", "La revisión anual", toward(1, 0))
    await kept(pool, org, "cerca.md", "Abrimos de lunes a viernes", toward(1, 0.5))
    both, near = await found(pool, org, vector=toward(1, 0), words="revisión")
    assert both.fused == pytest.approx(2 / (RRF_K + 1))
    assert near.fused == pytest.approx(1 / (RRF_K + 2))


@postgres
async def test_room_caps_each_branch_and_not_the_two_together(pool: Pool, org: str) -> None:
    for at in range(3):
        await kept(pool, org, f"cerca-{at}.md", "Abrimos de lunes a viernes", toward(1, at / 10))
    for at in range(3):
        await kept(pool, org, f"lejos-{at}.md", "revisión " * (at + 1), toward(0, 1, at / 10))
    hits = await found(pool, org, vector=toward(1, 0), words="revisión", room=2)
    assert sorted(paths(hits)) == ["cerca-0.md", "cerca-1.md", "lejos-1.md", "lejos-2.md"]


@postgres
async def test_a_row_no_term_matched_never_enters_the_word_branch(pool: Pool, org: str) -> None:
    await kept(pool, org, "horario.md", "Abrimos de lunes a viernes", toward(1, 0))
    await kept(pool, org, "tarifas.md", "La revisión anual cuesta cuarenta euros", toward(0, 1))
    hits = await found(pool, org, vector=toward(1, 0), words="lunes", room=1)
    assert paths(hits) == ["horario.md"]


@postgres
async def test_a_scope_with_no_rows_finds_nothing(pool: Pool, org: str) -> None:
    await kept(pool, org, "horario.md", "Abrimos de lunes a viernes", toward(1, 0))
    async with pool.connection() as connection:
        hits = await hybrid(
            connection, chunks_of(org, "otra"), vector=toward(1, 0), words="lunes", room=30
        )
    assert hits == []


@postgres
async def test_the_scope_sees_one_orgs_rows_and_never_anothers(pool: Pool, org: str) -> None:
    await kept(pool, org, "horario.md", "Abrimos de lunes a viernes", toward(1, 0))
    other = await orgs.create(pool, "otra-clinica", "Otra Clinica")
    assert await found(pool, other.id, vector=toward(1, 0), words="lunes") == []


@postgres
async def test_the_vector_branch_alone_can_be_narrowed_and_the_word_branch_still_finds(
    pool: Pool, org: str
) -> None:
    await kept(pool, org, "tarifas.md", "La revisión anual", toward(1, 0))
    narrowed = SearchedTable(
        name="knowledge_chunks",
        text_index="knowledge_chunks_text_bm25",
        scope=SCOPE,
        params={"org": org, "base": BASE},
        columns=("path",),
        same_space=sql.SQL("false"),
    )
    async with pool.connection() as connection:
        by_words = await hybrid(connection, narrowed, vector=toward(1, 0), words="revisión", room=5)
        by_vector = await hybrid(connection, narrowed, vector=toward(1, 0), words="nada", room=5)
    assert [hit.fused for hit in by_words] == [pytest.approx(1 / (RRF_K + 1))]
    assert by_vector == []


@postgres
async def test_the_scan_goes_on_past_the_index_first_candidates_in_the_same_transaction(
    pool: Pool, org: str
) -> None:
    async with pool.connection() as connection, connection.transaction():
        await hybrid(connection, chunks_of(org), vector=toward(1, 0), words="lunes", room=5)
        setting = await (
            await connection.execute("SELECT current_setting('hnsw.iterative_scan') AS scan")
        ).fetchone()
    assert setting is not None
    assert setting["scan"] == "relaxed_order"


def a_hit(named: str, fused: float, cosine: float = 0.5) -> Hit:
    return Hit(id=named, fused=fused, cosine=cosine, row={})


def test_the_best_is_one_and_the_rest_are_their_share_of_it_best_first() -> None:
    scored = relative_to_the_best([a_hit("b", 0.5), a_hit("a", 1.0), a_hit("c", 0.25)])
    assert [(hit.id, hit.fused) for hit in scored] == [("a", 1.0), ("b", 0.5), ("c", 0.25)]


def test_a_tie_is_ordered_by_id_so_two_processes_answer_alike() -> None:
    tied = relative_to_the_best([a_hit("z", 1.0), a_hit("m", 1.0)])
    assert [hit.id for hit in tied] == ["m", "z"]


def test_nothing_found_is_nothing_and_a_zero_best_divides_nothing() -> None:
    assert relative_to_the_best([]) == []
    assert [hit.fused for hit in relative_to_the_best([a_hit("a", 0.0)])] == [0.0]


def test_the_relative_score_tops_at_one_and_reads_best_first() -> None:
    fused = [1 / (RRF_K + rank) for rank in (3, 1, 2)] + [2 / (RRF_K + 1)]
    scores = [
        hit.fused
        for hit in relative_to_the_best([a_hit(str(at), score) for at, score in enumerate(fused)])
    ]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 1.0


def test_the_top_cosine_is_the_best_hits_and_nothing_found_is_zero() -> None:
    assert top_cosine([a_hit("a", 1.0, cosine=0.2), a_hit("b", 0.5, cosine=0.61)]) == 0.61
    assert top_cosine([]) == 0.0


@pytest.mark.parametrize(
    ("cosine", "evidence"),
    [
        (0.45, "strong"),
        (0.8, "strong"),
        (0.44, "weak"),
        (0.30, "weak"),
        (0.29, "none"),
        (0.0, "none"),
    ],
)
def test_the_best_cosine_says_how_strong_the_evidence_is(cosine: float, evidence: str) -> None:
    assert evidence_of(cosine) == evidence

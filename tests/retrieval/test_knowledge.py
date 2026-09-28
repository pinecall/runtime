"""Tests for knowledge bases: the cutter, the bases on Postgres, the search, the golden."""

import hashlib
import math
from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.domain.agent import KnowledgeFile
from pinecall.domain.errors import WrongModel
from pinecall.domain.names import PRODUCTION, SANDBOX, Env
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.providers.catalog import Embedding
from pinecall.retrieval import _search, knowledge
from pinecall.retrieval._search import NDCG_AT
from pinecall.retrieval.embed import Embedder, estimated_tokens, halfvec
from pinecall.retrieval.knowledge import (
    A_HEADING,
    A_SENTENCE_END,
    CHUNK_TOKENS,
    HEADING_JOINT,
    HEADING_SEPARATOR,
    Answered,
    Found,
    Piece,
    Push,
    Question,
    SearchQuery,
    answers,
    body_of,
    chunks_kept,
    chunks_of,
    cut,
    indexed_text,
    score_docs,
    where,
)
from pinecall.tenancy import orgs
from tests.conftest import postgres
from tests.fakes.embeddings import Embeddings, Meanings

URL = "https://embeddings.test/v1"
KEY = "a-key-of-the-box"
FLAT = Embedding(vendor="acme", url=URL, model="embed-flat-1", shape="embeddings")
CONTEXTUAL = Embedding(vendor="acme", url=URL, model="embed-context-1", shape="contextual")
ANOTHER = Embedding(vendor="acme", url=URL, model="embed-flat-2", shape="embeddings")

# The words the fake vendor gives an axis each; every other word is near-orthogonal to all.
KNOWN = ("turnos", "teléfono", "por", "cuarenta", "café")

THE_BASE = "clinica"
PUSHED_AT = 1_000.0
ANA = "m_ana"
BETO = "m_beto"

CLINICA = KnowledgeFile(
    "clinica.md",
    "# Clínica Norte\n\n## Horarios\n\nAbrimos de lunes a viernes, de nueve a siete. El teléfono "
    "atiende en ese horario.\n\n## Turnos\n\nLos turnos se piden por teléfono o por la web, con "
    "dos días de antelación.\n",
)
TARIFAS = KnowledgeFile(
    "tarifas.md",
    "# Tarifas\n\n## Revisión\n\nLa revisión cuesta cuarenta euros.\n\n### Con radiografía\n\n"
    "Con radiografía, cincuenta.\n\n## Limpieza\n\nLa limpieza dental cuesta sesenta euros.\n",
)
VENDING = KnowledgeFile(
    "maquinas.md", "# Máquinas\n\n## Café\n\nLa máquina de café acepta monedas y tarjetas.\n"
)


# ── the cutter ──


def joined(*headings: str) -> str:
    """A heading path, as the cutter and the golden write it."""
    return HEADING_SEPARATOR.join(headings)


def test_every_piece_carries_the_path_of_the_headings_over_it() -> None:
    pieces = chunks_of(TARIFAS.path, TARIFAS.text)
    assert [piece.heading for piece in pieces] == [
        joined("Tarifas", "Revisión"),
        joined("Tarifas", "Revisión", "Con radiografía"),
        joined("Tarifas", "Limpieza"),
    ]
    assert [piece.ordinal for piece in pieces] == [0, 1, 2]
    assert {piece.path for piece in pieces} == {"tarifas.md"}


def test_the_text_a_piece_is_indexed_as_opens_with_its_heading_path() -> None:
    first = chunks_of(TARIFAS.path, TARIFAS.text)[0]
    assert (
        indexed_text(first)
        == joined("Tarifas", "Revisión") + "\n\nLa revisión cuesta cuarenta euros."
    )
    assert first.text == "La revisión cuesta cuarenta euros."


def test_a_heading_with_nothing_under_it_is_no_piece_at_all() -> None:
    headings = [piece.heading for piece in chunks_of(TARIFAS.path, TARIFAS.text)]
    assert "Tarifas" not in headings


def test_what_comes_before_the_first_heading_is_a_piece_with_no_path() -> None:
    pieces = chunks_of("clinica.md", "Somos la Clínica Norte.\n\n# Horarios\n\nDe nueve a seis.\n")
    assert [(piece.heading, indexed_text(piece)) for piece in pieces] == [
        (None, "Somos la Clínica Norte."),
        ("Horarios", "Horarios\n\nDe nueve a seis."),
    ]


def test_a_fourth_level_heading_is_body_and_never_a_cut() -> None:
    [only] = chunks_of("faq.md", "# Preguntas\n\n#### Una fina\n\nSu respuesta.\n")
    assert only.heading == "Preguntas"
    assert only.text == "#### Una fina\n\nSu respuesta."


def test_a_hash_inside_a_code_fence_is_a_comment_and_never_a_heading() -> None:
    text = (
        "# Instalar\n\n```bash\n# instala las dependencias\nmake install\n```\n\n"
        "~~~\n## tampoco\n~~~\n\nY listo.\n"
    )
    [only] = chunks_of("instalar.md", text)
    assert only.heading == "Instalar"
    assert "# instala las dependencias" in only.text
    assert "## tampoco" in only.text


def test_a_file_with_no_headings_is_read_by_paragraph_groups() -> None:
    paragraphs = [f"El párrafo {n} tiene unas cuantas palabras." for n in range(4)]
    [only] = chunks_of("notas.md", "\n\n".join(paragraphs))
    assert only.heading is None
    assert only.text == HEADING_JOINT.join(paragraphs)


def test_a_long_section_is_cut_at_its_paragraphs_and_every_cut_keeps_the_path() -> None:
    paragraph = " ".join(["palabra"] * 60)
    pieces = chunks_of("largo.md", "# Guía\n\n## Todo\n\n" + "\n\n".join([paragraph] * 12))
    assert len(pieces) > 1
    assert {piece.heading for piece in pieces} == {joined("Guía", "Todo")}
    assert all(estimated_tokens(indexed_text(piece)) <= CHUNK_TOKENS for piece in pieces)
    assert all(piece.text.startswith("palabra") for piece in pieces)
    assert [piece.ordinal for piece in pieces] == list(range(len(pieces)))


def test_one_paragraph_over_the_cap_is_cut_at_its_sentences() -> None:
    sentence = " ".join(["palabra"] * 40) + "."
    pieces = chunks_of("denso.md", " ".join([sentence] * 10))
    assert len(pieces) > 1
    assert all(estimated_tokens(piece.text) <= CHUNK_TOKENS for piece in pieces)
    assert all(piece.text.endswith(".") for piece in pieces)


def test_an_empty_file_is_no_piece() -> None:
    assert chunks_of("vacio.md", "") == []
    assert chunks_of("solo.md", "# Solo un título\n") == []


def test_the_indexed_text_and_the_body_are_each_others_inverse() -> None:
    headed = Piece("tarifas.md", "Tarifas", 0, "cuarenta")
    bare = Piece("tarifas.md", None, 0, "cuarenta")
    assert body_of(indexed_text(headed), headed.heading) == "cuarenta"
    assert body_of(indexed_text(bare), bare.heading) == "cuarenta"


def test_a_files_front_matter_is_not_a_chunk() -> None:
    text = (
        '---\nsource: https://example.com/about\ntitle: "Who we are"\nscraped_at: 2026-08-22\n'
        "---\n\n## What this page answers\n\nWe clean offices."
    )
    [only] = chunks_of("about.md", text)
    assert "scraped_at" not in indexed_text(only)
    assert only.heading == "What this page answers"


def test_a_file_that_opens_with_a_rule_and_never_closes_it_is_left_alone() -> None:
    pieces = chunks_of("rule.md", "---\n\n## Tarifas\n\ncuarenta euros")
    assert [piece.heading for piece in pieces] == [None, "Tarifas"]
    assert pieces[0].text == "---"


def test_the_front_matter_of_a_file_with_no_headings_is_still_dropped() -> None:
    pieces = chunks_of("flat.md", "---\ntitle: x\n---\n\nWe clean offices.")
    assert [piece.text for piece in pieces] == ["We clean offices."]


# Random Markdown, seeded: headings of level 1 to 3 and paragraphs of up to 120 words.
LETTERS = "abcdefghijklmnopqrstuvwxyzáéíóúñ0123456789"


class Drawn:
    """A seeded stream of numbers: the same Markdown on every run, and nothing to install."""

    def __init__(self, seed: int) -> None:
        """Start the stream at the seed."""
        self.state = seed

    def between(self, low: int, high: int) -> int:
        """The next number from low to high, both included."""
        self.state = (self.state * 6364136223846793005 + 1442695040888963407) % 2**64
        return low + (self.state >> 33) % (high - low + 1)


def random_files(count: int) -> list[str]:
    """Markdown files of random headings and paragraphs of up to 120 words."""
    drawn = Drawn(20260928)

    def word() -> str:
        return "".join(
            LETTERS[drawn.between(0, len(LETTERS) - 1)] for _ in range(drawn.between(1, 12))
        )

    def block() -> str:
        if drawn.between(1, 10) <= 3:
            return f"{'#' * drawn.between(1, 3)} {word()}"
        return " ".join(word() for _ in range(drawn.between(1, 120)))

    return ["\n\n".join(block() for _ in range(drawn.between(0, 12))) for _ in range(count)]


def test_every_piece_is_under_the_cap_or_is_one_sentence_that_could_not_be_cut() -> None:
    for text in random_files(200):
        for piece in chunks_of("any.md", text):
            cannot_be_cut = len(A_SENTENCE_END.split(piece.text)) == 1
            assert estimated_tokens(indexed_text(piece)) <= CHUNK_TOKENS or cannot_be_cut


def test_the_pieces_are_numbered_from_zero_carry_the_path_and_lose_no_word() -> None:
    for text in random_files(200):
        pieces = chunks_of("any.md", text)
        assert [piece.ordinal for piece in pieces] == list(range(len(pieces)))
        assert {piece.path for piece in pieces} <= {"any.md"}
        words = [word for piece in pieces for word in piece.text.split()]
        prose = [line for line in text.split("\n") if not A_HEADING.match(line)]
        assert words == [word for line in prose for word in line.split()]


def test_a_file_is_cut_once_and_carries_the_hash_of_its_text() -> None:
    item = cut(TARIFAS)
    assert item.sha256 == hashlib.sha256(TARIFAS.text.encode()).hexdigest()
    assert list(item.pieces) == chunks_of(TARIFAS.path, TARIFAS.text)
    assert Push(THE_BASE, (item, cut(CLINICA)), PUSHED_AT).chunks == 5


# ── the bases on Postgres ──


@pytest.fixture
def meanings() -> Meanings:
    """The vendor, answering vectors that point at the words a text says."""
    return Meanings(words=KNOWN)


@pytest.fixture
async def client(meanings: Meanings) -> AsyncIterator[httpx.AsyncClient]:
    """The box's HTTP client, reaching only the fake vendor."""
    async with httpx.AsyncClient(transport=meanings.transport()) as http:
        yield http


@pytest.fixture
def embedder(client: httpx.AsyncClient) -> Embedder:
    """The box's embedder on the flat shape."""
    return Embedder(FLAT, KEY, client)


@pytest.fixture
async def org(pool: Pool) -> str:
    """An org of its own."""
    return (await orgs.create(pool, "clinica-norte", "Clinica Norte")).id


def at(org: str, env: Env = PRODUCTION, holder: str = "") -> Scope:
    """The org's corner in the world, the holder's when one is named."""
    return Scope(org, env, holder)


async def pushed(
    pool: Pool, embedder: Embedder, where: Scope, base: str, *files: KnowledgeFile
) -> None:
    """The files pushed as the base, whole."""
    folder = Push(base, tuple(cut(file) for file in files), PUSHED_AT)
    await knowledge.put(pool, embedder, where, folder)


async def put_alone(
    pool: Pool, embedder: Embedder, where: Scope, base: str, file: KnowledgeFile
) -> None:
    """One file put into the base beside the others."""
    await knowledge.put_file(pool, embedder, where, Push(base, (cut(file),), PUSHED_AT))


async def found(
    pool: Pool, embedder: Embedder, where: Scope, query: str, *bases: str, k: int = 8
) -> list[Found]:
    """What a search of the bases hands a turn, with no floor."""
    lookup = SearchQuery(query=query, k=k, bases=dict.fromkeys(bases or (THE_BASE,), 0.0))
    return (await knowledge.search(pool, embedder, where, lookup)).found


async def chunks_of_the_base(pool: Pool, org: str) -> list[int]:
    """The chunk count of every base the org's own corner lists."""
    return [item.chunks for item in await knowledge.bases(pool, at(org))]


@postgres
async def test_a_push_counts_its_chunks_and_a_second_push_replaces_the_first(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    assert [(item.base, item.chunks) for item in await knowledge.bases(pool, at(org))] == [
        (THE_BASE, 5)
    ]
    await pushed(pool, embedder, at(org), THE_BASE, TARIFAS)
    assert await chunks_of_the_base(pool, org) == [3]
    assert {item.path for item in await found(pool, embedder, at(org), "turnos teléfono")} == {
        "tarifas.md"
    }


@postgres
async def test_a_push_keeps_its_files_and_a_file_is_put_and_taken_out_alone(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    listed = await knowledge.files(pool, at(org), THE_BASE)
    assert listed is not None
    assert [(item.path, item.chunks, item.chars) for item in listed] == [
        ("clinica.md", 2, len(CLINICA.text)),
        ("tarifas.md", 3, len(TARIFAS.text)),
    ]
    read = await knowledge.file(pool, at(org), THE_BASE, "tarifas.md")
    assert read is not None
    assert read.text == TARIFAS.text

    horarios = KnowledgeFile(
        "faq/horarios.md", "# Horarios\n\n## Semana\n\nTurnos de nueve a veinte."
    )
    await put_alone(pool, embedder, at(org), THE_BASE, horarios)
    assert await chunks_of_the_base(pool, org) == [6]
    assert (await found(pool, embedder, at(org), "nueve veinte semana"))[0].path == horarios.path

    shorter = KnowledgeFile("tarifas.md", "# Tarifas\n\nTodo cuesta cuarenta euros.\n")
    await put_alone(pool, embedder, at(org), THE_BASE, shorter)
    assert await chunks_of_the_base(pool, org) == [4]
    assert await chunks_kept(pool, at(org), THE_BASE, path="tarifas.md") == 1

    assert await knowledge.drop_file(pool, at(org), THE_BASE, "tarifas.md") is True
    assert await knowledge.drop_file(pool, at(org), THE_BASE, "tarifas.md") is False
    assert await chunks_of_the_base(pool, org) == [3]
    left = await knowledge.files(pool, at(org), THE_BASE)
    assert [item.path for item in left or []] == ["clinica.md", "faq/horarios.md"]


@postgres
async def test_a_file_begins_a_base_and_the_last_file_out_takes_the_base_with_it(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await put_alone(pool, embedder, at(org, SANDBOX), "nueva", TARIFAS)
    listed = await knowledge.bases(pool, at(org, SANDBOX))
    assert [(item.base, item.chunks) for item in listed] == [("nueva", 3)]
    assert await knowledge.drop_file(pool, at(org, SANDBOX), "nueva", "tarifas.md") is True
    assert await knowledge.bases(pool, at(org, SANDBOX)) == []


@postgres
async def test_a_second_push_forgets_the_files_the_folder_no_longer_has(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    await pushed(pool, embedder, at(org), THE_BASE, TARIFAS)
    listed = await knowledge.files(pool, at(org), THE_BASE)
    assert [item.path for item in listed or []] == ["tarifas.md"]


@postgres
async def test_the_base_row_says_which_model_wrote_the_vectors_and_at_which_width(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA)
    async with pool.connection() as connection:
        row = await (
            await connection.execute("select model, dimensions from knowledge_bases")
        ).fetchone()
    assert row is not None
    assert (row["model"], row["dimensions"]) == (FLAT.model, 1024)
    [listed] = await knowledge.bases(pool, at(org))
    assert (listed.model, listed.pushed_at) == (FLAT.model, PUSHED_AT)


@postgres
async def test_a_search_finds_a_chunk_by_a_heading_word_the_text_search_stems(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    first, *_ = await found(pool, embedder, at(org), "turno")
    assert first.heading == joined("Clínica Norte", "Turnos")
    assert first.text.startswith("Los turnos se piden")


@postgres
async def test_a_search_finds_a_chunk_by_words_the_text_search_drops_because_the_vector_keeps_them(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    first, *_ = await found(pool, embedder, at(org), "por")
    assert first.heading == joined("Clínica Norte", "Turnos")


@postgres
async def test_a_chunk_both_branches_find_scores_one_and_min_score_drops_the_rest(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    every = await found(pool, embedder, at(org), "turnos")
    assert every[0].heading == joined("Clínica Norte", "Turnos")
    assert every[0].score == 1.0
    assert all(chunk.score < 0.6 for chunk in every[1:])
    assert len(every) == 5
    floored = SearchQuery(query="turnos", k=8, bases={THE_BASE: 0.6})
    kept = (await knowledge.search(pool, embedder, at(org), floored)).found
    assert [chunk.heading for chunk in kept] == [joined("Clínica Norte", "Turnos")]


@postgres
async def test_k_caps_what_a_turn_is_handed(pool: Pool, embedder: Embedder, org: str) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    assert len(await found(pool, embedder, at(org), "cuarenta", k=1)) == 1
    assert len(await found(pool, embedder, at(org), "cuarenta", k=3)) == 3


@postgres
async def test_dropping_a_base_never_pushed_answers_false_and_a_pushed_one_true(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    assert await knowledge.drop(pool, at(org), "nunca") is False
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA)
    assert await knowledge.drop(pool, at(org), THE_BASE) is True
    assert await knowledge.bases(pool, at(org)) == []
    assert await found(pool, embedder, at(org), "horarios") == []
    assert await knowledge.drop(pool, at(org), THE_BASE) is False


@postgres
async def test_an_org_never_sees_another_orgs_base_of_the_same_name(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    other = (await orgs.create(pool, "otra", "Otra")).id
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA)
    await pushed(pool, embedder, at(other), THE_BASE, TARIFAS)
    assert {item.path for item in await found(pool, embedder, at(org), "cuarenta")} == {
        "clinica.md"
    }
    assert await chunks_of_the_base(pool, other) == [3]


@postgres
async def test_a_push_of_nothing_is_a_base_with_no_chunks(
    pool: Pool, embedder: Embedder, org: str, meanings: Meanings
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE)
    assert await chunks_of_the_base(pool, org) == [0]
    assert await found(pool, embedder, at(org), "horarios") == []
    assert meanings.inputs() == [["horarios"]]


@postgres
async def test_a_base_pushed_with_another_model_is_refused_naming_both_and_the_way_out(
    pool: Pool, embedder: Embedder, org: str, client: httpx.AsyncClient
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA)
    other = Embedder(ANOTHER, KEY, client)
    with pytest.raises(WrongModel) as refused:
        await found(pool, other, at(org), "horarios")
    assert str(refused.value) == (
        f"base {THE_BASE} was pushed with {FLAT.model}; this box embeds with {ANOTHER.model}: "
        "push it again with `pinecall docs push`"
    )


@postgres
async def test_the_model_is_checked_before_the_question_is_embedded(
    pool: Pool, embedder: Embedder, org: str, client: httpx.AsyncClient, meanings: Meanings
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA)
    asked_before = len(meanings.requests)
    with pytest.raises(WrongModel):
        await found(pool, Embedder(ANOTHER, KEY, client), at(org), "horarios")
    assert len(meanings.requests) == asked_before


@postgres
async def test_a_base_nobody_pushed_is_not_a_model_mismatch_but_an_empty_answer(
    pool: Pool, org: str, client: httpx.AsyncClient, meanings: Meanings
) -> None:
    other = Embedder(ANOTHER, KEY, client)
    searched = await knowledge.search(
        pool, other, at(org), SearchQuery(query="horarios", k=8, bases={"nunca": 0.0})
    )
    assert searched.found == []
    assert searched.evidence == "none"
    assert meanings.requests == []


@postgres
async def test_an_orgs_chunks_count_per_world_and_a_sandbox_push_never_eats_productions_room(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    assert await knowledge.kept(pool, org, PRODUCTION) == 0
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    await pushed(pool, embedder, at(org, SANDBOX), THE_BASE, TARIFAS)
    assert await knowledge.kept(pool, org, PRODUCTION) == 5
    assert await knowledge.kept(pool, org, SANDBOX) == 3
    await knowledge.drop(pool, at(org, SANDBOX), THE_BASE)
    assert await knowledge.kept(pool, org, SANDBOX) == 0
    assert await knowledge.kept(pool, org, PRODUCTION) == 5


@postgres
async def test_a_base_is_one_worlds_and_a_laptops_push_never_touches_the_boxs(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA)
    await pushed(pool, embedder, at(org, SANDBOX), THE_BASE, TARIFAS)
    deployed = {item.path for item in await found(pool, embedder, at(org), "turnos")}
    written = {item.path for item in await found(pool, embedder, at(org, SANDBOX), "turnos")}
    assert deployed == {CLINICA.path}
    assert written == {TARIFAS.path}
    assert [item.chunks for item in await knowledge.bases(pool, at(org, SANDBOX))] == [3]
    assert await knowledge.drop(pool, at(org, SANDBOX), THE_BASE) is True
    assert await found(pool, embedder, at(org, SANDBOX), "turnos") == []
    assert len(await found(pool, embedder, at(org), "turnos")) == 2


@postgres
async def test_the_chunks_a_push_is_counted_at_are_the_cut_it_writes(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    folder = Push(THE_BASE, (cut(CLINICA), cut(TARIFAS)), PUSHED_AT)
    await knowledge.put(pool, embedder, at(org), folder)
    assert await chunks_of_the_base(pool, org) == [folder.chunks]
    assert await chunks_kept(pool, at(org), THE_BASE) == folder.chunks


# ── one developer's own ──


@postgres
async def test_two_developers_push_their_own_and_neither_replaces_the_others(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org, SANDBOX, ANA), THE_BASE, CLINICA)
    await pushed(pool, embedder, at(org, SANDBOX, BETO), THE_BASE, TARIFAS)
    anas = await found(pool, embedder, at(org, SANDBOX, ANA), "turnos")
    betos = await found(pool, embedder, at(org, SANDBOX, BETO), "turnos")
    assert {item.path for item in anas} == {CLINICA.path}
    assert {item.path for item in betos} == {TARIFAS.path}


@postgres
async def test_a_developer_who_has_pushed_nothing_reads_the_orgs_own(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org, SANDBOX), THE_BASE, CLINICA)
    anas = at(org, SANDBOX, ANA)
    assert {item.path for item in await found(pool, embedder, anas, "turnos")} == {CLINICA.path}
    assert [item.base for item in await knowledge.bases(pool, anas)] == [THE_BASE]
    assert [item.path for item in await knowledge.files(pool, anas, THE_BASE) or []] == [
        CLINICA.path
    ]


@postgres
async def test_their_own_wins_over_the_orgs_own_once_they_have_pushed(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org, SANDBOX), THE_BASE, CLINICA)
    anas = at(org, SANDBOX, ANA)
    await pushed(pool, embedder, anas, THE_BASE, TARIFAS)
    assert {item.path for item in await found(pool, embedder, anas, "turnos")} == {TARIFAS.path}
    assert [item.chunks for item in await knowledge.bases(pool, anas)] == [3]


@postgres
async def test_a_drop_takes_your_own_copy_and_never_the_orgs(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org, SANDBOX), THE_BASE, CLINICA)
    anas = at(org, SANDBOX, ANA)
    await pushed(pool, embedder, anas, THE_BASE, TARIFAS)
    assert await knowledge.drop(pool, anas, THE_BASE) is True
    assert {item.path for item in await found(pool, embedder, anas, "turnos")} == {CLINICA.path}


@postgres
async def test_dropping_a_name_you_never_pushed_says_so_even_where_the_org_has_one(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org, SANDBOX), THE_BASE, CLINICA)
    assert await knowledge.drop(pool, at(org, SANDBOX, ANA), THE_BASE) is False
    assert await knowledge.drop_file(pool, at(org, SANDBOX, ANA), THE_BASE, CLINICA.path) is False
    assert len(await found(pool, embedder, at(org, SANDBOX), "turnos")) == 2


@postgres
async def test_the_chunks_a_world_keeps_count_every_holders_copy_because_the_rows_are_the_orgs(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org, SANDBOX, ANA), THE_BASE, CLINICA)
    await pushed(pool, embedder, at(org, SANDBOX, BETO), THE_BASE, TARIFAS)
    assert await knowledge.kept(pool, org, SANDBOX) == 5
    assert await knowledge.kept(pool, org, PRODUCTION) == 0


@postgres
async def test_a_second_base_with_nothing_relevant_in_it_takes_none_of_the_turns_slots(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    await pushed(pool, embedder, at(org), "vending", VENDING)
    every = await found(pool, embedder, at(org), "turnos teléfono", THE_BASE, "vending")
    assert [item.base for item in every[:2]] == [THE_BASE, THE_BASE]
    assert every[0].heading == joined("Clínica Norte", "Turnos")
    assert every[0].score == 1.0
    assert all(item.score < 1.0 for item in every if item.base == "vending")


# ── what a push sends the vendor ──

DISTANCE = """
select embedding <=> %(wanted)s::halfvec as distance from knowledge_chunks where text = %(text)s
"""


@postgres
async def test_a_file_is_one_document_so_a_chunk_is_embedded_seeing_its_neighbours(
    pool: Pool, org: str
) -> None:
    vendor = Embeddings()
    async with httpx.AsyncClient(transport=vendor.transport()) as http:
        await pushed(pool, Embedder(CONTEXTUAL, KEY, http), at(org), THE_BASE, CLINICA, TARIFAS)
    assert [len(document) for document in vendor.inputs()] == [2, 3]
    assert vendor.inputs()[0][0].startswith(joined("Clínica Norte", "Horarios"))
    assert vendor.inputs()[1][0].startswith(joined("Tarifas", "Revisión"))


@postgres
async def test_the_vectors_are_written_back_flat_in_the_order_the_files_were_cut(
    pool: Pool, embedder: Embedder, org: str, meanings: Meanings
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    async with pool.connection() as connection:
        rows = await (await connection.execute("select text from knowledge_chunks")).fetchall()
        assert len(rows) == 5
        for row in rows:
            wanted = meanings.vector(row["text"])
            length = math.sqrt(sum(value * value for value in wanted))
            its_own = {"text": row["text"], "wanted": halfvec([item / length for item in wanted])}
            distance = await (await connection.execute(DISTANCE, its_own)).fetchone()
            assert distance is not None
            assert distance["distance"] < 1e-3


@postgres
async def test_the_bases_row_keeps_the_model_that_answered_and_its_width(
    pool: Pool, org: str
) -> None:
    async with httpx.AsyncClient(transport=Embeddings().transport()) as http:
        await pushed(pool, Embedder(CONTEXTUAL, KEY, http), at(org), THE_BASE, CLINICA)
    [listed] = await knowledge.bases(pool, at(org))
    assert (listed.model, listed.chunks) == (CONTEXTUAL.model, 2)


@postgres
async def test_the_files_travel_beside_the_chunks_with_what_each_became(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    listed = await knowledge.files(pool, at(org), THE_BASE) or []
    assert [(item.path, item.chunks, item.sha256) for item in listed] == [
        (CLINICA.path, 2, hashlib.sha256(CLINICA.text.encode()).hexdigest()),
        (TARIFAS.path, 3, hashlib.sha256(TARIFAS.text.encode()).hexdigest()),
    ]
    assert [item.pushed_at for item in listed] == [PUSHED_AT, PUSHED_AT]


@postgres
async def test_a_second_push_of_the_same_text_sends_the_vendor_nothing(
    pool: Pool, embedder: Embedder, org: str, meanings: Meanings
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    sent = len(meanings.requests)
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    assert len(meanings.requests) == sent
    assert await chunks_of_the_base(pool, org) == [5]


@postgres
async def test_a_changed_file_is_embedded_alone_and_the_others_keep_their_rows(
    pool: Pool, embedder: Embedder, org: str, meanings: Meanings
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    before = await chunk_ids(pool, CLINICA.path)
    sent = len(meanings.requests)
    cheaper = KnowledgeFile("tarifas.md", "# Tarifas\n\n## Revisión\n\nLa revisión cuesta treinta.")
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, cheaper)
    assert meanings.inputs()[sent:] == [
        [joined("Tarifas", "Revisión") + "\n\nLa revisión cuesta treinta."]
    ]
    assert await chunk_ids(pool, CLINICA.path) == before
    assert await chunks_of_the_base(pool, org) == [3]


@postgres
async def test_a_whole_push_under_another_model_embeds_every_file_again(
    pool: Pool, embedder: Embedder, org: str, client: httpx.AsyncClient, meanings: Meanings
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    sent = len(meanings.requests)
    other = Embedder(ANOTHER, KEY, client)
    await pushed(pool, other, at(org), THE_BASE, CLINICA, TARIFAS)
    assert sum(len(texts) for texts in meanings.inputs()[sent:]) == 5
    [listed] = await knowledge.bases(pool, at(org))
    assert (listed.model, listed.chunks) == (ANOTHER.model, 5)
    assert len(await found(pool, other, at(org), "turnos")) == 5


@postgres
async def test_one_file_put_into_a_base_of_another_model_is_refused_before_it_is_embedded(
    pool: Pool, embedder: Embedder, org: str, client: httpx.AsyncClient, meanings: Meanings
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA)
    sent = len(meanings.requests)
    with pytest.raises(WrongModel):
        await put_alone(pool, Embedder(ANOTHER, KEY, client), at(org), THE_BASE, TARIFAS)
    assert len(meanings.requests) == sent


async def chunk_ids(pool: Pool, path: str) -> list[str]:
    """The ids of the file's chunk rows, in order."""
    async with pool.connection() as connection:
        rows = await (
            await connection.execute(
                "select id::text as id from knowledge_chunks where path = %(path)s order by id",
                {"path": path},
            )
        ).fetchall()
    return [row["id"] for row in rows]


# ── how strongly the answer is there ──


@postgres
async def test_a_question_the_base_answers_is_strong_evidence_and_one_it_does_not_is_none(
    pool: Pool, embedder: Embedder, org: str
) -> None:
    await pushed(pool, embedder, at(org), THE_BASE, CLINICA, TARIFAS)
    answered = await knowledge.search(
        pool, embedder, at(org), SearchQuery(query="turnos", k=8, bases={THE_BASE: 0.0})
    )
    unanswered = await knowledge.search(
        pool, embedder, at(org), SearchQuery(query="zzz qqq", k=8, bases={THE_BASE: 0.0})
    )
    assert answered.evidence == "strong"
    assert unanswered.top_cosine < 0.30
    assert unanswered.evidence == "none"
    assert unanswered.model == FLAT.model


# ── the golden ──

REVISION = joined("Tarifas", "Revisión")


def a_chunk(path: str = "tarifas.md", heading: str | None = REVISION) -> Found:
    """A chunk whose path and heading are all a golden reads."""
    return Found(id="c", base=THE_BASE, path=path, heading=heading, text="…", score=1.0, cosine=0.5)


def answered(expects: str, *chunks: Found) -> Answered:
    """A question answered with these chunks, best first."""
    return Answered(Question(asks="¿cuánto cuesta?", expects=expects), list(chunks))


def test_a_chunks_heading_path_is_the_file_and_the_headings_under_it() -> None:
    assert where(a_chunk()) == joined("tarifas.md", "Tarifas", "Revisión")
    assert where(a_chunk(heading=None)) == "tarifas.md"


def test_naming_a_file_alone_accepts_any_chunk_of_it() -> None:
    assert answers(joined("tarifas.md", "Tarifas", "Revisión"), "tarifas.md")


def test_naming_a_heading_accepts_that_section_and_what_is_under_it() -> None:
    assert answers(joined("tarifas.md", "Tarifas", "Revisión"), joined("tarifas.md", "Tarifas"))
    assert answers(
        joined("tarifas.md", "Tarifas", "Revisión"), joined("tarifas.md", "Tarifas", "Revisión")
    )


def test_a_file_never_answers_for_a_file_whose_name_merely_starts_the_same() -> None:
    assert not answers(joined("tarifas-2024.md", "Revisión"), "tarifas.md")


def test_a_question_whose_chunk_came_back_first_scores_everything() -> None:
    score = score_docs([answered("tarifas.md", a_chunk())], k=4)
    assert (score.figures.recall_at_k, score.figures.ndcg_at_10) == (1.0, 1.0)
    assert score.misses == []


def test_a_question_whose_chunk_came_back_second_still_counts_for_recall() -> None:
    score = score_docs([answered("tarifas.md", a_chunk(path="otro.md"), a_chunk())], k=4)
    assert score.figures.recall_at_k == 1.0
    assert score.figures.ndcg_at_10 == pytest.approx(0.6309, abs=0.001)


def test_a_question_whose_chunk_never_came_back_is_a_miss_the_person_can_read() -> None:
    score = score_docs([answered("tarifas.md", a_chunk(path="otro.md", heading="Horarios"))], k=4)
    assert (score.figures.recall_at_k, score.figures.ndcg_at_10) == (0.0, 0.0)
    [missed] = score.misses
    assert [where(item) for item in missed.found] == [joined("otro.md", "Horarios")]


def test_only_the_first_match_counts_so_a_base_that_repeats_itself_answered_once() -> None:
    score = score_docs([answered("tarifas.md", a_chunk(), a_chunk(), a_chunk())], k=4)
    assert score.figures.ndcg_at_10 == 1.0


def test_a_chunk_past_the_tenth_scores_nothing_because_the_model_never_saw_it() -> None:
    tail = [a_chunk(path=f"otro-{n}.md") for n in range(NDCG_AT)] + [a_chunk()]
    score = score_docs([answered("tarifas.md", *tail)], k=len(tail))
    assert score.figures.recall_at_k == 1.0
    assert score.figures.ndcg_at_10 == 0.0


def test_the_two_figures_are_the_share_over_every_question_asked() -> None:
    score = score_docs(
        [answered("tarifas.md", a_chunk()), answered("tarifas.md", a_chunk(path="otro.md"))], k=4
    )
    assert score.questions == 2
    assert (score.figures.recall_at_k, score.figures.ndcg_at_10) == (0.5, 0.5)


def test_a_golden_with_no_questions_scores_nothing_and_says_so() -> None:
    score = score_docs([], k=4)
    assert score.questions == 0
    assert (score.figures.recall_at_k, score.figures.ndcg_at_10) == (0.0, 0.0)


def test_a_question_that_wants_two_answers_and_got_one_of_them_is_half_recalled() -> None:
    figures = _search.figures([[1, None]])
    assert figures.recall_at_k == 0.5
    assert figures.ndcg_at_10 == pytest.approx(0.6131, abs=0.001)


def test_two_wanted_answers_ranked_low_are_recalled_whole_and_ordered_badly() -> None:
    figures = _search.figures([[5, 6]])
    assert figures.recall_at_k == 1.0
    assert figures.ndcg_at_10 == pytest.approx(0.4556, abs=0.001)


def test_a_question_that_wanted_nothing_scores_nothing_and_raises_nothing() -> None:
    figures = _search.figures([[]])
    assert (figures.recall_at_k, figures.ndcg_at_10) == (0.0, 0.0)


def test_a_question_that_wants_two_answers_and_got_the_top_two_is_perfect() -> None:
    figures = _search.figures([[1, 2]])
    assert (figures.recall_at_k, figures.ndcg_at_10) == (1.0, 1.0)

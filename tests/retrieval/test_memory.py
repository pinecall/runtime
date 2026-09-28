"""Contact memory: recall by both branches, extraction and its admission, the goldens."""

from datetime import datetime, timedelta

import httpx
import pytest

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import PRODUCTION, SANDBOX
from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.retrieval import memory
from pinecall.retrieval._search import RRF_K, Hit
from pinecall.retrieval.embed import Embedder
from pinecall.retrieval.memory import HALF_LIFE_DAYS, Answered, Held, Paging, Recall
from pinecall.wire.rest.retrieval import (
    MemoryGolden,
    MemoryQuestion,
)
from tests.conftest import postgres
from tests.fakes.acme import AcmeLLM
from tests.fakes.embeddings import Embeddings
from tests.retrieval.conftest import (
    CONTACT,
    HUNG_UP,
    LEARNED,
    MODEL,
    ARow,
    history_of,
    toward,
    written,
)


def answering(*vectors: list[float]) -> httpx.Response:
    """The vendor's reply to one request: these vectors, in order."""
    rows = [{"index": at, "embedding": vector} for at, vector in enumerate(vectors)]
    return httpx.Response(200, json={"data": rows})


async def recalled(pool: Pool, embedder: Embedder, scope: Scope, query: str) -> list[str]:
    """The texts a recall answers, best first."""
    found = await memory.recall(pool, embedder, scope, Recall(CONTACT, query, HUNG_UP))
    return [item.fact.text for item in found.facts]


# ── parsing ──


# ── admission ──


# ── what the model is shown ──


# ── the extraction golden ──


# ── ranking ──


def a_hit(named: str, fused: float, *, learned: datetime = HUNG_UP, confidence: float = 1.0) -> Hit:
    """A fused row as the hybrid statement answers it."""
    row = {
        "id": named,
        "contact": CONTACT,
        "category": None,
        "text": named,
        "valid_from": learned,
        "invalidated_at": None,
        "supersedes": None,
        "source_call": None,
        "model": MODEL,
        "confidence": confidence,
    }
    return Hit(id=named, fused=fused, cosine=0.5, row=row)


def ranked(*hits: Hit, k: int = 6) -> list[tuple[str, float]]:
    return [(item.fact.id, item.score) for item in memory.ranked(hits, now=HUNG_UP, k=k)]


def test_the_fact_both_branches_find_comes_before_the_ones_only_one_finds() -> None:
    both = a_hit("both", 2 / (RRF_K + 1))
    dense, sparse = a_hit("dense", 1 / (RRF_K + 1)), a_hit("sparse", 1 / (RRF_K + 2))
    assert [named for named, _ in ranked(sparse, dense, both)] == ["both", "dense", "sparse"]


def test_the_best_fact_scores_one_and_the_rest_are_their_share_of_it() -> None:
    (_, first), (_, second) = ranked(a_hit("a", 1 / (RRF_K + 1)), a_hit("b", 1 / (RRF_K + 2)))
    assert first == 1.0
    assert second == pytest.approx((RRF_K + 1) / (RRF_K + 2))


def test_a_fact_learned_a_half_life_ago_weighs_half_of_one_learned_now() -> None:
    assert memory.recency(HUNG_UP, HUNG_UP) == 1.0
    assert memory.recency(HUNG_UP - timedelta(days=HALF_LIFE_DAYS), HUNG_UP) == pytest.approx(0.5)
    assert memory.recency(HUNG_UP - timedelta(days=2 * HALF_LIFE_DAYS), HUNG_UP) == pytest.approx(
        0.25
    )


def test_a_fact_dated_after_now_weighs_no_more_than_one() -> None:
    assert memory.recency(HUNG_UP + timedelta(days=3), HUNG_UP) == 1.0


def test_of_two_facts_at_the_same_rank_the_more_recent_one_comes_first() -> None:
    old = a_hit("old", 1 / (RRF_K + 1), learned=HUNG_UP - timedelta(days=180))
    fresh = a_hit("fresh", 1 / (RRF_K + 1))
    assert ranked(old, fresh) == [("fresh", 1.0), ("old", pytest.approx(0.25))]


def test_a_fact_memory_was_less_sure_of_yields_to_one_it_was_sure_of() -> None:
    unsure = a_hit("unsure", 1 / (RRF_K + 1), confidence=0.5)
    sure = a_hit("sure", 1 / (RRF_K + 1))
    assert ranked(unsure, sure) == [("sure", 1.0), ("unsure", pytest.approx(0.5))]


def test_only_the_k_best_are_answered() -> None:
    hits = [a_hit(f"f{at}", 1 / (RRF_K + 1 + at)) for at in range(10)]
    assert [named for named, _ in ranked(*hits, k=3)] == ["f0", "f1", "f2"]


def test_no_candidates_is_no_facts() -> None:
    assert ranked() == []


def test_a_fact_nobody_is_sure_of_at_all_scores_zero_and_divides_nothing() -> None:
    assert ranked(a_hit("none", 1 / (RRF_K + 1), confidence=0.0)) == [("none", 0.0)]


# ── the memory golden's score ──

MORNINGS = "Prefiere mañanas"
PENICILLIN = "Alérgica a la penicilina"
VIDAL = "Paciente de la doctora Vidal desde 2024"


def answered(expects: list[str], *found: str) -> Answered:
    """A question answered with these facts, best first."""
    question = MemoryQuestion(
        holds=[MORNINGS, PENICILLIN], asks="¿le va bien el martes?", expects=expects
    )
    return Answered(question=question, found=found)


def test_a_fact_says_what_was_expected_when_it_is_the_same_sentence() -> None:
    assert memory.says(MORNINGS, MORNINGS)


def test_a_fact_that_says_more_than_the_golden_asked_for_is_still_the_fact() -> None:
    assert memory.says("Prefiere mañanas, nunca después de comer", MORNINGS)


def test_a_fact_that_says_less_than_the_golden_asked_for_is_not_the_fact() -> None:
    assert not memory.says("Alérgica", PENICILLIN)


def test_accents_and_case_are_not_what_a_golden_is_held_to() -> None:
    assert memory.says("prefiere MANANAS para las citas", MORNINGS)
    assert memory.says(PENICILLIN, "alergica a la penicilina")


def test_a_golden_written_across_two_lines_matches_a_fact_written_on_one() -> None:
    assert memory.says(VIDAL, "Paciente de la doctora\n   Vidal desde 2024")


def test_another_fact_of_the_same_contact_is_not_the_one_that_was_expected() -> None:
    assert not memory.says(PENICILLIN, MORNINGS)


def test_the_fact_that_came_back_first_scores_everything_and_misses_nothing() -> None:
    score = memory.score_memory([answered([MORNINGS], MORNINGS, PENICILLIN)], k=6)
    assert (score.questions, score.k) == (1, 6)
    assert (score.figures.recall_at_k, score.figures.ndcg_at_10) == (1.0, 1.0)
    assert score.misses == ()


def test_a_fact_that_came_back_second_counts_for_recall_and_costs_the_ranking() -> None:
    score = memory.score_memory([answered([MORNINGS], PENICILLIN, MORNINGS)], k=6)
    assert score.figures.recall_at_k == 1.0
    assert score.figures.ndcg_at_10 == pytest.approx(0.6309, abs=0.001)


def test_a_question_memory_did_not_answer_names_what_was_missing_and_what_came_instead() -> None:
    score = memory.score_memory([answered([MORNINGS], PENICILLIN, VIDAL)], k=6)
    assert (score.figures.recall_at_k, score.figures.ndcg_at_10) == (0.0, 0.0)
    (missed,) = score.misses
    assert missed.missing == (MORNINGS,)
    assert missed.found == (PENICILLIN, VIDAL)


def test_a_question_that_wants_two_facts_and_got_one_of_them_is_half_recalled_and_a_miss() -> None:
    score = memory.score_memory([answered([MORNINGS, PENICILLIN], MORNINGS, VIDAL)], k=6)
    assert score.figures.recall_at_k == 0.5
    (missed,) = score.misses
    assert missed.missing == (PENICILLIN,)


def test_only_the_first_fact_that_says_it_counts_so_memory_that_repeats_itself_answered_once() -> (
    None
):
    score = memory.score_memory([answered([MORNINGS], MORNINGS, MORNINGS, MORNINGS)], k=6)
    assert score.figures.ndcg_at_10 == 1.0


def test_the_figures_are_the_share_over_every_question_asked() -> None:
    score = memory.score_memory(
        [answered([MORNINGS], MORNINGS), answered([PENICILLIN], MORNINGS)], k=6
    )
    assert (score.questions, score.figures.recall_at_k, score.figures.ndcg_at_10) == (2, 0.5, 0.5)


def test_a_golden_with_no_questions_scores_nothing_and_says_so() -> None:
    score = memory.score_memory([], k=6)
    assert (score.questions, score.figures.recall_at_k, score.misses) == (0, 0.0, ())


# ── recall ──


@postgres
async def test_a_fact_is_recalled_by_its_words_when_its_vector_says_nothing(
    pool: Pool, embedder: Embedder, vendor: Embeddings, scope: Scope
) -> None:
    await written(pool, scope, ARow("prefiere turnos por la mañana", like=(0, 1)))
    await written(pool, scope, ARow("vive en Montevideo con su perro", like=(1, 0)))
    vendor.script.append(answering(toward(0, 0, 1)))
    found = await memory.recall(pool, embedder, scope, Recall(CONTACT, "turno de mañana", HUNG_UP))
    assert found.facts[0].fact.text == "prefiere turnos por la mañana"
    assert found.facts[0].score == 1.0


@postgres
async def test_a_fact_is_recalled_by_its_vector_when_its_words_say_nothing(
    pool: Pool, embedder: Embedder, vendor: Embeddings, scope: Scope
) -> None:
    await written(pool, scope, ARow("le gusta el café cortado", like=(1, 0)))
    await written(pool, scope, ARow("vive en Montevideo con su perro", like=(0, 1)))
    vendor.script.append(answering(toward(1, 0)))
    found = await memory.recall(pool, embedder, scope, Recall(CONTACT, "bebida caliente", HUNG_UP))
    assert found.facts[0].fact.text == "le gusta el café cortado"
    assert found.facts[0].score == 1.0


@postgres
async def test_the_fact_both_branches_find_comes_first(
    pool: Pool, embedder: Embedder, vendor: Embeddings, scope: Scope
) -> None:
    await written(pool, scope, ARow("prefiere turnos por la tarde", like=(0, 1)))
    await written(pool, scope, ARow("vive en Montevideo", like=(1, 0)))
    await written(pool, scope, ARow("prefiere turnos por la mañana", like=(1, 0.3)))
    vendor.script.append(answering(toward(1, 0)))
    found = await memory.recall(
        pool, embedder, scope, Recall(CONTACT, "turnos por la mañana", HUNG_UP)
    )
    first, *rest = found.facts
    assert (first.fact.text, first.score) == ("prefiere turnos por la mañana", 1.0)
    assert {item.fact.text for item in rest} == {
        "prefiere turnos por la tarde",
        "vive en Montevideo",
    }
    assert all(item.score < 1.0 for item in rest)


@postgres
async def test_an_invalidated_fact_is_not_recalled_but_is_in_the_history(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    await written(pool, scope, ARow("prefiere turnos por la mañana", invalidated=HUNG_UP))
    await written(pool, scope, ARow("prefiere turnos por la tarde"))
    assert await recalled(pool, embedder, scope, "turnos") == ["prefiere turnos por la tarde"]
    history = await memory.history(pool, scope, CONTACT)
    assert [fact.text for fact in history] == [
        "prefiere turnos por la tarde",
        "prefiere turnos por la mañana",
    ]
    assert history[0].invalidated_at is None
    assert history[1].invalidated_at == HUNG_UP


@postgres
async def test_a_recall_as_of_a_moment_reads_what_held_then(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    await written(pool, scope, ARow("prefiere turnos por la mañana", invalidated=HUNG_UP))
    await written(pool, scope, ARow("prefiere turnos por la tarde", learned=HUNG_UP))
    then = await memory.recall(
        pool, embedder, scope, Recall(CONTACT, "turnos", HUNG_UP, as_of=LEARNED)
    )
    assert [item.fact.text for item in then.facts] == ["prefiere turnos por la mañana"]


@postgres
async def test_a_fact_of_another_model_is_out_of_the_dense_branch_and_still_found_by_words(
    pool: Pool, embedder: Embedder, vendor: Embeddings, scope: Scope
) -> None:
    await written(pool, scope, ARow("prefiere el café cortado", like=(1, 0), model="old-model"))
    await written(pool, scope, ARow("vive en Montevideo", like=(1, 0)))
    vendor.script.extend([answering(toward(1, 0)), answering(toward(1, 0))])
    by_words = await recalled(pool, embedder, scope, "café cortado")
    by_vector = await recalled(pool, embedder, scope, "algo caliente")
    assert set(by_words) == {"prefiere el café cortado", "vive en Montevideo"}
    assert by_vector == ["vive en Montevideo"]


@postgres
async def test_the_best_cosine_of_this_embedders_facts_says_how_strong_the_evidence_is(
    pool: Pool, embedder: Embedder, vendor: Embeddings, scope: Scope
) -> None:
    await written(pool, scope, ARow("prefiere la tarde", like=(1, 0)))
    await written(pool, scope, ARow("vive en Pocitos", like=(0, 1), model="old-model"))
    vendor.script.extend(
        answering(item) for item in (toward(1, 0), toward(0.4, 0.9165), toward(0, 1))
    )
    bands = [
        (await memory.recall(pool, embedder, scope, Recall(CONTACT, "x", HUNG_UP))).evidence
        for _ in range(3)
    ]
    assert bands == ["strong", "weak", "none"]


@postgres
async def test_a_contact_with_no_facts_recalls_nothing_with_no_evidence(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    found = await memory.recall(pool, embedder, scope, Recall(CONTACT, "turnos", HUNG_UP))
    assert (found.facts, found.top_cosine, found.evidence) == ([], 0.0, "none")


# ── remember ──


# ── the quota, forgetting, holding ──


@postgres
async def test_the_facts_an_org_keeps_are_counted_across_its_contacts_and_history_is_not(
    pool: Pool, scope: Scope
) -> None:
    assert await memory.kept(pool, scope.org, PRODUCTION) == 0
    await written(pool, scope, ARow("prefiere la mañana"))
    await written(pool, scope, ARow("vive en Montevideo", contact="another-contact"))
    assert await memory.kept(pool, scope.org, PRODUCTION) == 2
    await written(pool, scope, ARow("prefería la tarde", invalidated=HUNG_UP))
    assert await memory.kept(pool, scope.org, PRODUCTION) == 2
    assert await memory.forget(pool, scope, CONTACT) == 2
    assert await memory.kept(pool, scope.org, PRODUCTION) == 1


@postgres
async def test_forget_takes_every_row_of_the_contact_and_answers_how_many(
    pool: Pool, scope: Scope
) -> None:
    for text in ("uno", "dos", "tres"):
        await written(pool, scope, ARow(text))
    await written(pool, scope, ARow("cuatro", contact="somebody-else"))
    assert await memory.forget(pool, scope, CONTACT) == 3
    assert await memory.forget(pool, scope, CONTACT) == 0
    assert await history_of(pool, scope) == []
    assert await history_of(pool, scope, "somebody-else") == ["cuatro"]


@postgres
async def test_hold_writes_the_sentences_it_was_given_and_asks_no_model(
    pool: Pool, embedder: Embedder, scope: Scope, models: list[AcmeLLM]
) -> None:
    sentences = ("prefiere turnos por la mañana", "vive en Montevideo con su perro")
    await memory.hold(pool, embedder, scope, Held(CONTACT, sentences, HUNG_UP))
    history = await memory.history(pool, scope, CONTACT)
    assert {fact.text for fact in history} == set(sentences)
    assert [fact.valid_from for fact in history] == [HUNG_UP, HUNG_UP]
    assert [fact.category for fact in history] == [None, None]
    assert models == []


@postgres
async def test_a_fact_a_golden_held_is_recalled_by_the_two_branches_a_turn_reads(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    sentences = ("prefiere turnos por la mañana", "vive en Montevideo con su perro", "zzz qqq xxx")
    await memory.hold(pool, embedder, scope, Held(CONTACT, sentences, HUNG_UP))
    found = await memory.recall(
        pool, embedder, scope, Recall(CONTACT, "turno de mañana", HUNG_UP, k=2)
    )
    assert found.facts[0].fact.text == "prefiere turnos por la mañana"
    assert found.facts[0].score == 1.0
    assert len(found.facts) == 2


@postgres
async def test_the_facts_a_golden_held_carry_this_embedder_and_go_with_one_forget(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    await memory.hold(
        pool, embedder, scope, Held(CONTACT, ("prefiere la mañana", "vive en Pocitos"), HUNG_UP)
    )
    assert [fact.model for fact in await memory.history(pool, scope, CONTACT)] == [MODEL, MODEL]
    assert await memory.forget(pool, scope, CONTACT) == 2
    assert await history_of(pool, scope) == []


@postgres
async def test_a_contacts_facts_are_one_worlds_and_a_test_call_never_reaches_the_real_ones(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    sandbox = Scope(scope.org, SANDBOX)
    await memory.hold(
        pool, embedder, scope, Held(CONTACT, ("prefiere que la llamen a la tarde",), HUNG_UP)
    )
    await memory.hold(
        pool, embedder, sandbox, Held(CONTACT, ("dice que su pedido no llegó",), HUNG_UP)
    )
    assert await recalled(pool, embedder, scope, "pedido") == ["prefiere que la llamen a la tarde"]
    assert await recalled(pool, embedder, sandbox, "pedido") == ["dice que su pedido no llegó"]
    assert await memory.kept(pool, scope.org, PRODUCTION) == 1
    assert await memory.kept(pool, scope.org, SANDBOX) == 1
    assert await memory.forget(pool, sandbox, CONTACT) == 1
    assert await history_of(pool, scope) == ["prefiere que la llamen a la tarde"]


# ── one developer's own ──

ANA = "m_ana"
BETO = "m_beto"


def of(scope: Scope, holder: str) -> Scope:
    return Scope(scope.org, SANDBOX, holder)


@postgres
async def test_two_developers_testing_the_same_number_do_not_read_each_others_facts(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    await memory.hold(
        pool, embedder, of(scope, ANA), Held(CONTACT, ("dice que prefiere la mañana",), HUNG_UP)
    )
    await memory.hold(
        pool, embedder, of(scope, BETO), Held(CONTACT, ("dice que prefiere la tarde",), HUNG_UP)
    )
    assert await recalled(pool, embedder, of(scope, ANA), "cuándo prefiere") == [
        "dice que prefiere la mañana"
    ]
    assert await recalled(pool, embedder, of(scope, BETO), "cuándo prefiere") == [
        "dice que prefiere la tarde"
    ]


@postgres
async def test_a_corner_with_no_facts_recalls_nothing_and_never_the_orgs(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    await memory.hold(
        pool, embedder, of(scope, ""), Held(CONTACT, ("lo que aprendió CI",), HUNG_UP)
    )
    assert await recalled(pool, embedder, of(scope, ANA), "qué sabe") == []


@postgres
async def test_forgetting_a_contact_takes_your_corner_and_leaves_the_others(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    await memory.hold(pool, embedder, of(scope, ANA), Held(CONTACT, ("lo de ana",), HUNG_UP))
    await memory.hold(pool, embedder, of(scope, BETO), Held(CONTACT, ("lo de beto",), HUNG_UP))
    assert await memory.forget(pool, of(scope, ANA), CONTACT) == 1
    assert await recalled(pool, embedder, of(scope, ANA), "lo") == []
    assert await recalled(pool, embedder, of(scope, BETO), "lo") == ["lo de beto"]


@postgres
async def test_the_quota_counts_every_holder_of_a_world_and_never_the_other_world(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    await memory.hold(pool, embedder, of(scope, ANA), Held(CONTACT, ("uno",), HUNG_UP))
    await memory.hold(pool, embedder, of(scope, BETO), Held(CONTACT, ("dos",), HUNG_UP))
    await memory.hold(pool, embedder, scope, Held(CONTACT, ("tres",), HUNG_UP))
    assert await memory.kept(pool, scope.org, SANDBOX) == 2
    assert await memory.kept(pool, scope.org, PRODUCTION) == 1


@postgres
async def test_the_history_of_a_contact_is_the_asking_corners(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    await memory.hold(pool, embedder, of(scope, ANA), Held(CONTACT, ("lo de ana",), HUNG_UP))
    await memory.hold(pool, embedder, of(scope, BETO), Held(CONTACT, ("lo de beto",), HUNG_UP))
    assert await history_of(pool, of(scope, ANA)) == ["lo de ana"]


# ── what an agent's calls taught ──

TAUGHT = """
INSERT INTO call_log_head (log, agent, call, org)
VALUES (%(call)s, %(agent)s, %(call)s, %(org)s), (%(other)s, 'another-agent', %(other)s, %(org)s)
"""


@postgres
async def test_an_agents_facts_are_the_current_ones_its_calls_taught_newest_first(
    pool: Pool, scope: Scope
) -> None:
    agent, call, other = "clinica-norte", "CA_taught", "CA_other"
    async with pool.connection() as connection:
        await connection.execute(
            TAUGHT, {"call": call, "agent": agent, "other": other, "org": scope.org}
        )
    old = await written(pool, scope, ARow("prefiere la tarde", source=call, learned=LEARNED))
    new = await written(
        pool, scope, ARow("tiene un perro", source=call, learned=HUNG_UP, contact="+34611")
    )
    await written(pool, scope, ARow("otro agente lo sabe", source=other))
    await written(pool, scope, ARow("ya no vale", source=call, invalidated=HUNG_UP))
    await written(pool, scope, ARow("un golden lo trajo"))
    first = await memory.taught_by(pool, scope, agent, page=Paging(limit=1))
    assert [fact.id for fact in first.facts] == [new]
    rest = await memory.taught_by(pool, scope, agent, page=Paging(after=first.next, limit=5))
    assert ([fact.id for fact in rest.facts], rest.next) == ([old], None)
    found = await memory.taught_by(pool, scope, agent, page=Paging(q="PERRO"))
    assert [fact.id for fact in found.facts] == [new]
    every = await memory.taught_by(pool, scope, None, page=Paging())
    assert sorted(every.agents.values()) == sorted([agent, agent, "another-agent"])
    assert await memory.invalidated(pool, scope, new, at=HUNG_UP) is True
    assert await memory.invalidated(pool, scope, new, at=HUNG_UP) is False
    assert await memory.invalidated(pool, Scope(scope.org, SANDBOX), old, at=HUNG_UP) is False
    history = await memory.history(pool, scope, "+34611")
    assert history[0].invalidated_at == HUNG_UP


@postgres
async def test_a_search_word_is_matched_as_written_and_never_as_a_pattern(
    pool: Pool, scope: Scope
) -> None:
    async with pool.connection() as connection:
        await connection.execute(
            TAUGHT, {"call": "CA_1", "agent": "clinica-norte", "other": "CA_2", "org": scope.org}
        )
    await written(pool, scope, ARow("paga el 100% por adelantado", source="CA_1"))
    await written(pool, scope, ARow("paga la mitad", source="CA_1"))
    found = await memory.taught_by(pool, scope, None, page=Paging(q="100%"))
    assert [fact.text for fact in found.facts] == ["paga el 100% por adelantado"]
    assert (await memory.taught_by(pool, scope, None, page=Paging(q="%"))).facts[0].text == (
        "paga el 100% por adelantado"
    )


@postgres
async def test_a_cursor_that_is_not_one_is_refused_and_never_read_as_the_first_page(
    pool: Pool, scope: Scope
) -> None:
    for after in ("page-2", "2026-09-10T12:00:00+00:00|not-an-id", "yesterday|"):
        with pytest.raises(DeclarationRefused, match="is not a cursor"):
            await memory.taught_by(pool, scope, None, page=Paging(after=after))


# ── re-embedding ──


@postgres
async def test_only_stale_facts_are_asked_for_and_each_is_written_with_this_model(
    pool: Pool, embedder: Embedder, vendor: Embeddings, scope: Scope
) -> None:
    await written(pool, scope, ARow("Alérgica a la penicilina.", model="old-model"))
    await written(pool, scope, ARow("Prefiere mañanas.", model="old-model", contact="+34611"))
    await written(pool, Scope(scope.org, SANDBOX), ARow("Vive en Pocitos.", model="old-model"))
    await written(pool, scope, ARow("Ya está al día."))
    assert await memory.reembed(pool, embedder) == 3
    assert vendor.inputs() == [
        ["Alérgica a la penicilina.", "Prefiere mañanas.", "Vive en Pocitos."]
    ]
    kept = await memory.history(pool, scope, CONTACT)
    assert {fact.model for fact in kept} == {MODEL}


@postgres
async def test_the_facts_go_to_the_embedder_in_batches(
    pool: Pool, embedder: Embedder, vendor: Embeddings, scope: Scope
) -> None:
    for at in range(5):
        await written(pool, scope, ARow(f"hecho {at}", model="old-model"))
    assert await memory.reembed(pool, embedder, batch=2) == 5
    assert [len(batch) for batch in vendor.inputs()] == [2, 2, 1]
    assert await memory.reembed(pool, embedder, batch=2) == 0


@postgres
async def test_nothing_stale_asks_the_embedder_nothing(
    pool: Pool, embedder: Embedder, vendor: Embeddings, scope: Scope
) -> None:
    await written(pool, scope, ARow("Ya está al día."))
    assert await memory.reembed(pool, embedder) == 0
    assert vendor.requests == []


# ── a memory golden, run ──


@postgres
async def test_a_golden_writes_each_questions_facts_asks_them_and_leaves_no_contact_behind(
    pool: Pool, embedder: Embedder, scope: Scope
) -> None:
    golden = MemoryGolden(
        questions=[
            MemoryQuestion(
                holds=[MORNINGS, PENICILLIN], asks="prefiere mañanas", expects=[MORNINGS]
            ),
            MemoryQuestion(
                holds=["Vive en Pocitos"], asks="dónde vive", expects=["Vive en Pocitos"]
            ),
        ]
    )
    score = await memory.ask_golden(pool, embedder, scope, golden, at=HUNG_UP)
    assert (score.model, score.questions, score.k, score.recall_at_k) == (MODEL, 2, 6, 1.0)
    assert await memory.kept(pool, scope.org, PRODUCTION) == 0

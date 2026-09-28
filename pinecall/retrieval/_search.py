"""The hybrid search over one table: nearest vectors and best BM25 words, fused by rank in SQL."""

import dataclasses
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from statistics import fmean
from typing import Literal

from psycopg import sql
from psycopg.rows import DictRow

from pinecall.postgres.pool import Connection
from pinecall.retrieval.embed import halfvec

type Evidence = Literal["strong", "weak", "none"]


# Cormack's constant: a rank weighs 1 / (60 + rank), so cosine and BM25 are never compared.
RRF_K = 60


# The cutoff of nDCG, past any k a turn is handed.
NDCG_AT = 10


CANDIDATES_PER_BRANCH = 30


# Measured on a golden of real questions; data for the reader and the judge, never a gate.
STRONG = 0.45


NONE_BELOW = 0.30


# `hnsw.iterative_scan` makes the index scan past its first 40 candidates when the scope filters
# them out: one tenant's rows among every tenant's would otherwise come back short, or empty.
ITERATIVE_SCAN = "SET LOCAL hnsw.iterative_scan = relaxed_order"


# `<@>` is BM25 negated, lower is better, and 0 when no term matched: those rows are no match.
HYBRID = """
WITH dense AS (
    SELECT id, RANK() OVER (ORDER BY distance) AS r FROM (
        SELECT id, embedding <=> %(q)s::halfvec AS distance FROM {table}
        WHERE ({scope}) AND ({same_space})
        ORDER BY embedding <=> %(q)s::halfvec
        LIMIT %(room)s
    ) AS nearest
),
sparse AS (
    SELECT id, RANK() OVER (ORDER BY score) AS r FROM (
        SELECT id, score FROM (
            SELECT id, text <@> to_bm25query(%(words)s, {text_index}) AS score FROM {table}
            WHERE ({scope})
        ) AS scored
        WHERE score < 0
        ORDER BY score
        LIMIT %(room)s
    ) AS worded
),
fusion AS (
    SELECT coalesce(dense.id, sparse.id) AS id,
        (coalesce(1.0 / ({k} + dense.r), 0) + coalesce(1.0 / ({k} + sparse.r), 0))::float8 AS fused
    FROM dense FULL OUTER JOIN sparse ON dense.id = sparse.id
)
SELECT fusion.id::text AS hit_id, fusion.fused AS hit_fused,
    1 - (found.embedding <=> %(q)s::halfvec) AS hit_cosine, {columns}
FROM fusion JOIN {table} AS found ON found.id = fusion.id
ORDER BY fusion.fused DESC, fusion.id
"""


@dataclass(frozen=True)
class SearchedTable:
    """What differs between the tables searched: which rows a query sees and what a hit carries."""

    name: str
    # to_bm25query reads its statistics by the index's name.
    text_index: str
    # A WHERE clause over the table, its placeholders named: `q`, `words` and `room` are taken.
    scope: sql.Composable
    params: Mapping[str, object]
    columns: tuple[str, ...]
    # Added to the scope of the vector branch alone: a row embedded by another model is found by
    # its words only.
    same_space: sql.Composable = field(default_factory=lambda: sql.SQL("true"))


@dataclass(frozen=True)
class Hit:
    """One row found: its id, its fused score, its cosine to the query, and the columns asked."""

    id: str
    fused: float
    cosine: float
    row: DictRow


@dataclass(frozen=True)
class Figures:
    """Recall at k, and nDCG at 10."""

    recall_at_k: float
    ndcg_at_10: float


async def hybrid(
    connection: Connection, table: SearchedTable, *, vector: list[float], words: str, room: int
) -> list[Hit]:
    """Up to `room` rows from each branch, fused by reciprocal rank, the best first."""
    query = sql.SQL(HYBRID).format(
        table=sql.Identifier(table.name),
        text_index=sql.Literal(table.text_index),
        scope=table.scope,
        same_space=table.same_space,
        k=sql.Literal(RRF_K),
        columns=sql.SQL(", ").join(sql.Identifier("found", column) for column in table.columns),
    )
    params = {**table.params, "q": halfvec(vector), "words": words, "room": room}
    async with connection.transaction():
        await connection.execute(ITERATIVE_SCAN)
        rows = await (await connection.execute(query, params)).fetchall()
    return [
        Hit(
            id=row["hit_id"],
            fused=row["hit_fused"],
            cosine=row["hit_cosine"],
            row={column: row[column] for column in table.columns},
        )
        for row in rows
    ]


# A fused score means nothing across queries: relative to the best, the top is always 1.0.
def relative_to_the_best(hits: list[Hit]) -> list[Hit]:
    """Each hit's fused score divided by the best, the best first and ties by id."""
    best = max((hit.fused for hit in hits), default=0.0)
    ordered = sorted(hits, key=lambda hit: (-hit.fused, hit.id))
    return [dataclasses.replace(hit, fused=hit.fused / best if best else 0.0) for hit in ordered]


def top_cosine(hits: list[Hit]) -> float:
    """The best cosine among the hits; 0.0 when there are none."""
    return max((hit.cosine for hit in hits), default=0.0)


def evidence_of(cosine: float) -> Evidence:
    """How strongly the best cosine says the question is answered here."""
    if cosine >= STRONG:
        return "strong"
    if cosine < NONE_BELOW:
        return "none"
    return "weak"


# nDCG is normalised by the ideal order for as many answers as the question wants.
def figures(ranks: Sequence[Sequence[int | None]]) -> Figures:
    """The two figures over the ranks of each question's wanted answers; None is not found."""
    if not ranks:
        return Figures(recall_at_k=0.0, ndcg_at_10=0.0)
    return Figures(
        recall_at_k=fmean(_share_found(question) for question in ranks),
        ndcg_at_10=fmean(_normalised(question) for question in ranks),
    )


def _share_found(ranks: Sequence[int | None]) -> float:
    if not ranks:
        return 0.0
    return sum(rank is not None for rank in ranks) / len(ranks)


def _normalised(ranks: Sequence[int | None]) -> float:
    if not ranks:
        return 0.0
    ideal = sum(_discounted(place) for place in range(1, min(len(ranks), NDCG_AT) + 1))
    return sum(_discounted(rank) for rank in ranks) / ideal


# Past the tenth the model never saw it.
def _discounted(rank: int | None) -> float:
    if rank is None or rank > NDCG_AT:
        return 0.0
    return 1.0 / math.log2(rank + 1)

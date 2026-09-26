"""The memory suite's Postgres: this run's schema with public behind it, an org, a contact, rows."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, cast
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]  # pyright: ignore[reportMissingTypeStubs]
import pytest
from pgvector import HalfVector

from pinecall.db import Pool
from pinecall.memory import PgvectorMemory
from pinecall.providers.registry import Chat
from pinecall.types import Brought, Model
from tests.session.fake_llm import FakeLLM, Scripted
from tests.support.postgres import Dev
from tests.support.vectors import HASH_MODEL, HashEmbedder, a_vector

# When every fact of this suite was learned, and when a call that remembers hangs up.
LEARNED = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
HUNG_UP = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

_create_pool = cast("Any", asyncpg.create_pool)  # pyright: ignore[reportUnknownMemberType]

# The org's own corner, spelled here rather than defaulted: 0021 drops the column's DEFAULT on
# purpose, so no policy lives in the table and every writer says whose a row is.
_A_ROW = """
INSERT INTO contact_memories
    (org, env, holder, contact, text, category, embedding, valid_from, invalidated_at,
     confidence, model, source_call)
VALUES ($1, 'production', '', $2, $3, $4, $5::text::halfvec, $6, $7, $8, $9, $10)
RETURNING id
"""


# The pool the runtime holds has public alone on its path; the suite's schema is its own, so the
# extensions' types and operators, which live in public, have to be reachable behind it.
@pytest.fixture
async def pool(postgres: Dev) -> AsyncIterator[Pool]:
    """A pool on this run's schema, with public behind it for halfvec and <@>."""
    pool = await _create_pool(
        postgres.dsn, server_settings={"search_path": f"{postgres.schema}, public"}
    )
    try:
        yield cast(Pool, pool)
    finally:
        await pool.close()


@pytest.fixture
async def org(pool: Pool) -> str:
    """A tenant of this test's own: contact_memories references it."""
    id = f"org-{uuid4().hex[:12]}"
    await pool.execute("INSERT INTO orgs (id, slug, name) VALUES ($1, $1, $1)", id)
    return id


@pytest.fixture
def contact() -> str:
    """A contact nobody else in this schema remembers."""
    return f"contact-{uuid4().hex[:12]}"


# What memory builds its model with: every remember answered by the one script, and each model
# kept so a test can read what it was asked.
class ScriptedModels:
    """A Models that answers the same thing every time, and remembers every model it built."""

    def __init__(self, answer: str = "[]") -> None:
        self.answer = answer
        self.built: list[FakeLLM] = []

    def __call__(self, asked: Model | None, brought: Brought) -> Chat:  # noqa: ARG002
        """One fresh scripted model per call, the way the real Models builds a plugin."""
        model = FakeLLM(Scripted(chunks=(self.answer,)))
        self.built.append(model)
        return model


@pytest.fixture
def models() -> ScriptedModels:
    """The model memory reaches for; a test writes `models.answer` before it remembers."""
    return ScriptedModels()


@pytest.fixture
def memory(pool: Pool, models: ScriptedModels) -> PgvectorMemory:
    """The memory under test: this schema, the hash embedder, the scripted model."""
    return PgvectorMemory(pool, HashEmbedder(), models)


# A row written straight into the table, with a vector of the test's choosing: `like` is the text
# the vector is hashed from, so a fact can share words with a query and no direction, or the
# other way round — which is how a test tells the two branches apart.
async def a_row(
    pool: Pool,
    org: str,
    contact: str,
    text: str,
    *,
    like: str | None = None,
    category: str | None = "preference",
    learned: datetime = LEARNED,
    invalidated: datetime | None = None,
    confidence: float = 1.0,
    model: str = HASH_MODEL,
    source: str | None = None,
) -> str:
    """One fact in the table; its id. `model` is whose vectors these are: the suite's."""
    vector = HalfVector(a_vector(like if like is not None else text)).to_text()
    row = await pool.fetchrow(
        _A_ROW,
        org,
        contact,
        text,
        category,
        vector,
        learned,
        invalidated,
        confidence,
        model,
        source,
    )
    assert row is not None
    return str(row["id"])

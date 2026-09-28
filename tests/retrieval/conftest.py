"""What the memory and extraction tests share: a contact, a policy, a scripted model, a call."""

import math
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest

from pinecall.domain.scope import Scope
from pinecall.postgres.pool import Pool
from pinecall.providers.catalog import Embedding
from pinecall.retrieval import memory
from pinecall.retrieval.embed import Embedder, halfvec
from pinecall.tenancy import orgs
from tests.fakes.acme import AcmeLLM
from tests.fakes.embeddings import Embeddings

MODEL = "embed-flat-1"

FLAT = Embedding(vendor="acme-embed", url="https://embed.test/v1", model=MODEL, shape="embeddings")

CONTACT = "+59899000001"

LEARNED = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)

HUNG_UP = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)

A_ROW = """
INSERT INTO contact_memories
    (org, env, holder, contact, text, category, embedding, valid_from, invalidated_at,
     confidence, model, source_call)
VALUES (%(org)s, %(env)s, %(holder)s, %(contact)s, %(text)s, %(category)s,
    %(embedding)s::halfvec, %(learned)s, %(invalidated)s, %(confidence)s, %(model)s, %(source)s)
RETURNING id::text AS id
"""


def toward(*weights: float) -> list[float]:
    """A unit vector of 1024 whose first values are the weights given."""
    length = math.sqrt(sum(weight * weight for weight in weights))
    return [weight / length for weight in weights] + [0.0] * (1024 - len(weights))


@dataclass(frozen=True)
class ARow:
    """A fact as a test writes it straight into the table."""

    text: str
    like: tuple[float, ...] = (1.0,)
    category: str | None = "preference"
    learned: datetime = LEARNED
    invalidated: datetime | None = None
    confidence: float = 1.0
    model: str = MODEL
    source: str | None = None
    contact: str = CONTACT


@pytest.fixture
def vendor() -> Embeddings:
    """The embedder's vendor: every vector the same unless a test scripts the next replies."""
    return Embeddings()


@pytest.fixture
async def embedder(vendor: Embeddings) -> AsyncIterator[Embedder]:
    """The box's embedder, reaching only the fake vendor."""
    async with httpx.AsyncClient(transport=vendor.transport()) as http:
        yield Embedder(FLAT, "a-key", http)


@pytest.fixture
async def scope(pool: Pool) -> Scope:
    """An org's own corner in production."""
    org = await orgs.create(pool, "clinica-norte", "Clinica Norte")
    return Scope(org.id)


@pytest.fixture
def models(acme: str, monkeypatch: pytest.MonkeyPatch) -> list[AcmeLLM]:
    """Every scripted model built through providers.build, in order."""
    built: list[AcmeLLM] = []

    class Kept(AcmeLLM):
        def __init__(
            self,
            *,
            api_key: str,
            model: str = "acme-1",
            temperature: float = 1.0,
            replies: list[list[str | dict[str, object]]] | None = None,
        ) -> None:
            super().__init__(api_key=api_key, model=model, temperature=temperature, replies=replies)
            built.append(self)

    monkeypatch.setattr(sys.modules[f"livekit.plugins.{acme}"], "LLM", Kept)
    return built


async def written(pool: Pool, scope: Scope, row: ARow) -> str:
    """A fact written straight into the table; its id."""
    params = {
        "org": scope.org,
        "env": scope.env,
        "holder": scope.holder,
        "contact": row.contact,
        "text": row.text,
        "category": row.category,
        "embedding": halfvec(toward(*row.like)),
        "learned": row.learned,
        "invalidated": row.invalidated,
        "confidence": row.confidence,
        "model": row.model,
        "source": row.source,
    }
    async with pool.connection() as connection:
        found = await (await connection.execute(A_ROW, params)).fetchone()
    assert found is not None
    return str(found["id"])


async def history_of(pool: Pool, scope: Scope, contact: str = CONTACT) -> list[str]:
    return [fact.text for fact in await memory.history(pool, scope, contact)]

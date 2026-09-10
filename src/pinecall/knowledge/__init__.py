"""The knowledge base: a tenant's files chunked, embedded and searched in Postgres."""

from pinecall.knowledge.protocol import Knowledge
from pinecall.knowledge.store import Base, PgKnowledge

__all__ = ["Base", "Knowledge", "PgKnowledge"]

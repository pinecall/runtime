"""The knowledge base: a tenant's files chunked, embedded and searched in Postgres."""

from pinecall.knowledge.rendering import chunks_as_text
from pinecall.knowledge.store import Base, PgKnowledge

__all__ = ["Base", "PgKnowledge", "chunks_as_text"]

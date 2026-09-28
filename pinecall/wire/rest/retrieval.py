"""The bodies of the knowledge and memory doors."""

from pydantic import Field

from pinecall.domain.names import Channel
from pinecall.wire.frames import WireModel
from pinecall.wire.parts import (
    KnowledgeFile,
)

# ── knowledge bases ──


# The base is replaced whole, never merged.
class KnowledgePush(WireModel):
    """PUT /v1/knowledge/{base}, the body: the tenant's folder as of now."""

    files: list[KnowledgeFile]


class KnowledgePushed(WireModel):
    """PUT /v1/knowledge/{base}, the answer: the base, the chunks it became, how long it took."""

    base: str
    chunks: int
    took_ms: float


class KnowledgeBase(WireModel):
    """One base as the list draws it: its size, the model that wrote it, when it was pushed."""

    base: str
    chunks: int
    model: str
    pushed_at: float


class KnowledgeList(WireModel):
    """GET /v1/knowledge: every base the key's scope reads."""

    bases: list[KnowledgeBase]


class KnowledgeUse(WireModel):
    """One base and the agents whose settings attach it."""

    base: str
    agents: list[str]


class KnowledgeUses(WireModel):
    """GET /v1/knowledge/attached: which agents read each base, off their newest settings."""

    bases: list[KnowledgeUse]


class KnowledgeFileRow(WireModel):
    """One file of a base as the list draws it."""

    path: str
    chars: int
    chunks: int
    pushed_at: float


# `kept` is false for a base whose files were never stored, only its chunks.
class KnowledgeFiles(WireModel):
    """GET /v1/knowledge/{base}: every file of the base, never its text."""

    base: str
    kept: bool
    files: list[KnowledgeFileRow]


class KnowledgeFileRead(WireModel):
    """GET /v1/knowledge/{base}/files/{path}: one file, text and all."""

    path: str
    text: str
    chunks: int
    pushed_at: float


class KnowledgeFilePut(WireModel):
    """PUT /v1/knowledge/{base}/files/{path}, the body: the file's whole text."""

    text: str


class KnowledgeFilePushed(WireModel):
    """PUT /v1/knowledge/{base}/files/{path}, the answer: the file, its chunks, how long it took."""

    base: str
    path: str
    chunks: int
    took_ms: float


# A chunk answers when its file and heading path start with `expects`, never by substring.
class GoldenQuestion(WireModel):
    """One question of a base's golden: what somebody asks, and the chunk that should answer."""

    asks: str
    expects: str


class KnowledgeGolden(WireModel):
    """POST /v1/knowledge/{base}/eval, the body: the questions a base is held to."""

    questions: list[GoldenQuestion]
    k: int | None = None


class GoldenMiss(WireModel):
    """One question whose chunk was not among the k returned, and what came back instead."""

    asks: str
    expects: str
    found: list[str]


class KnowledgeScore(WireModel):
    """POST /v1/knowledge/{base}/eval, the answer: recall@k and nDCG@10, computed with no model."""

    base: str
    model: str
    questions: int
    k: int
    recall_at_k: float
    ndcg_at_10: float
    took_ms: float
    misses: list[GoldenMiss]


# ── memory ──


# A fact is never updated, only superseded, so the history keeps every version.
class ContactFact(WireModel):
    """One fact of a contact's history, with the two dates that bound it."""

    id: str | None = None
    text: str
    category: str | None = None
    source: str | None = None
    valid_from: float
    invalidated_at: float | None


class ContactMemory(WireModel):
    """GET /v1/contacts/{contact}/memory: every fact ever kept of the contact, current first."""

    facts: list[ContactFact]


class Forgotten(WireModel):
    """DELETE /v1/contacts/{contact}/memory and /v1/memory/facts/{id}: how many facts went."""

    forgotten: int


class AgentFact(WireModel):
    """One current fact, among the contacts an agent's calls taught."""

    id: str
    contact: str
    text: str
    category: str | None
    written_at: float


class AgentMemory(WireModel):
    """GET /v1/agents/{slug}/memory: the facts the agent's calls taught, newest first, a page."""

    facts: list[AgentFact]
    next: str | None


class OrgFact(WireModel):
    """One current fact of the org, with the agent whose call taught it."""

    id: str
    agent: str
    contact: str
    text: str
    category: str | None
    written_at: float


class OrgMemory(WireModel):
    """GET /v1/memory: the org's current facts from every agent, newest first, a page."""

    facts: list[OrgFact]
    next: str | None = None


# A question brings its own facts: written to a scratch contact, asked, and forgotten.
class MemoryQuestion(WireModel):
    """One question of a memory golden: what memory holds, what is asked, what should come back."""

    holds: list[str]
    asks: str
    expects: list[str]


class MemoryGolden(WireModel):
    """POST /v1/contacts/memory/eval, the body: the questions memory is held to."""

    questions: list[MemoryQuestion]
    k: int | None = None


class MemoryMiss(WireModel):
    """One question memory did not answer whole: what it missed, and what came back instead."""

    asks: str
    missing: list[str]
    found: list[str]


class MemoryScore(WireModel):
    """POST /v1/contacts/memory/eval, the answer: recall@k and nDCG@10, computed with no model."""

    model: str
    questions: int
    k: int
    recall_at_k: float
    ndcg_at_10: float
    took_ms: float
    misses: list[MemoryMiss]


# A category is the agent's own word, a value is a literal the caller said, a supersession an id:
# nothing is judged by comparing two sentences.
class ExtractionExpected(WireModel):
    """What must come of one call's hang-up."""

    writes: list[str] = Field(default_factory=list[str])
    never: list[str] = Field(default_factory=list[str])
    never_says: list[str] = Field(default_factory=list[str])
    invalidates: list[str] = Field(default_factory=list[str])


class ExtractionGolden(WireModel):
    """One call written down, what memory holds before it, and what memory must make of it."""

    name: str
    said: list[tuple[str, str]]
    holds: list[str] = Field(default_factory=list[str])
    plants: list[str] = Field(default_factory=list[str])
    channel: Channel = "phone"
    expect: ExtractionExpected = Field(default_factory=ExtractionExpected)


class ExtractionCases(WireModel):
    """POST /v1/agents/{slug}/memory/extraction, the body: one model call per case."""

    cases: list[ExtractionGolden]


class ExtractionBroke(WireModel):
    """One check a case did not hold, and the evidence in a sentence."""

    check: str
    detail: str


class ExtractionJudged(WireModel):
    """One case run: what memory would have kept, what admission refused, what did not hold."""

    name: str
    held: bool
    wrote: list[str] = Field(default_factory=list[str])
    refused: list[str] = Field(default_factory=list[str])
    broke: list[ExtractionBroke] = Field(default_factory=list[ExtractionBroke])


class ExtractionRun(WireModel):
    """POST /v1/agents/{slug}/memory/extraction, the answer: the model, how many held, each case."""

    agent: str
    model: str
    cases: int
    held: int
    took_ms: float
    results: list[ExtractionJudged]

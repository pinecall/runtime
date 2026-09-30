"""An org and what it may do: its quotas, its admission, what the box lends it."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import parse_slug

# Names match the DB columns and the wire. The first four are flows (consumed or open now); the
# rest are stocks (rows kept), which lets a plan disable a feature with a zero; the budget is
# dollars a month, both worlds together.
type QuotaName = Literal[
    "minutes",
    "messages",
    "agents",
    "concurrent_calls",
    "memory_facts",
    "knowledge_chunks",
    "numbers",
    "seats",
    "llm_tokens",
    "hosted_apps",
    "budget_usd",
]


# Seeded by the schema; the first key is issued against it and CLI verbs fall back to it.
DEFAULT_ORG = "default"


@dataclass(frozen=True)
class Org:
    """An organisation: immutable id, editable slug, and display name."""

    id: str
    slug: str
    name: str

    def __post_init__(self) -> None:
        if not self.id:
            raise DeclarationRefused("an org has an id")
        parse_slug(self.slug)


# None is no limit (the self-hosted default). Zero is a real limit that refuses everything.
@dataclass(frozen=True)
class Quotas:
    """An org's quota limits, plus its monthly budget and the box keys lent to it."""

    minutes: int | None = None
    messages: int | None = None
    agents: int | None = None
    concurrent_calls: int | None = None
    memory_facts: int | None = None
    knowledge_chunks: int | None = None
    # Only numbers bought on the box's carrier account; imported numbers do not count.
    numbers: int | None = None
    # Invited and active members; disabled members hold no seat.
    seats: int | None = None
    llm_tokens: int | None = None
    # Apps the box runs for the org from sources it uploaded.
    hosted_apps: int | None = None
    # Whole US dollars a calendar month, both worlds together: a new call is refused past it.
    budget_usd: int | None = None
    # Box vendor keys the org may use: None all, empty none, else `vendor` or `vendor/model`.
    lends: frozenset[str] | None = None

    def __post_init__(self) -> None:
        for name, limit in self.limits.items():
            if limit is None or limit >= 0:
                continue
            if name == "budget_usd":
                raise DeclarationRefused(f"a budget is dollars, and cannot be {limit}")
            raise DeclarationRefused(f"a quota is a count, and {name} cannot be {limit}")

    @property
    def limits(self) -> Mapping[QuotaName, int | None]:
        """Return every limit by its name."""
        return {
            "minutes": self.minutes,
            "messages": self.messages,
            "agents": self.agents,
            "concurrent_calls": self.concurrent_calls,
            "memory_facts": self.memory_facts,
            "knowledge_chunks": self.knowledge_chunks,
            "numbers": self.numbers,
            "seats": self.seats,
            "llm_tokens": self.llm_tokens,
            "hosted_apps": self.hosted_apps,
            "budget_usd": self.budget_usd,
        }

    def reached(self, quota: QuotaName, used: float) -> int | None:
        """Return the limit when `used` is at or past it, else None."""
        limit = self.limits[quota]
        return limit if limit is not None and used >= limit else None

    def exceeded(self, quota: QuotaName, keeping: float) -> int | None:
        """Return the limit when `keeping` would pass it, else None; for pushes sized up front."""
        limit = self.limits[quota]
        return limit if limit is not None and keeping > limit else None

    def switched_off(self, quota: QuotaName) -> bool:
        """Return whether the quota is zero: the feature is not in the plan."""
        return self.limits[quota] == 0


QUOTAS: tuple[QuotaName, ...] = (
    "minutes",
    "messages",
    "agents",
    "concurrent_calls",
    "memory_facts",
    "knowledge_chunks",
    "numbers",
    "seats",
    "llm_tokens",
    "hosted_apps",
)

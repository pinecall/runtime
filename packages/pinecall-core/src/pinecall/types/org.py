"""Org: the tenant — whose agents, keys, routes, calls and usage these are — and its quotas."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from pinecall.types.refusal import DeclarationRefused

# The org a box has when nobody has made a second one: `migrate up` issues the first key against
# it, the keys and routes verbs fall back to it, and the schema seeds it as the first row.
DEFAULT_ORG = "default"

# A slug is what an operator types and a URL carries: lowercase, digits, a dash between words. An
# id is minted from it and never changes; the slug may be renamed later, which is why there are two.
_A_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

# What a quota is about. The names are the columns and the wire's, spelled once. The first four
# are a FLOW — what the org consumed since it existed, or holds open right now. The rest are a
# STOCK — how much of a table the org may keep standing — which is how a plan switches memory and
# retrieval off, caps the numbers it is sold and the people it seats, without the runtime learning
# what a plan is. docs/decisions/orgs.md.
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
]
QUOTAS: tuple[QuotaName, ...] = (
    "minutes",
    "messages",
    "agents",
    "concurrent_calls",
    "memory_facts",
    "knowledge_chunks",
    # The numbers bought for the org on the box's own carrier account, a STOCK: the ones a tenant
    # imports from its own account are its own and count against nothing here.
    "numbers",
    # The people the org may seat: invited and active together, because an invitation sent is a
    # seat taken. A disabled member keeps their row and holds none.
    "seats",
    # The input and output tokens of every model the org's calls ran, as call.summary reports
    # them: a FLOW like minutes, and the one a written conversation is held to on every turn.
    "llm_tokens",
)


@dataclass(frozen=True)
class Org:
    """One organisation: the stable id every row names it by, the slug people type, its name."""

    id: str
    slug: str
    name: str

    def __post_init__(self) -> None:
        if not self.id:
            raise DeclarationRefused("an org has an id")
        if not _A_SLUG.match(self.slug):
            raise DeclarationRefused(
                f"a slug is lowercase letters, digits and dashes, not {self.slug!r}"
            )


# None is no limit, which is what a self-hosted box means by default: the mechanism is here and
# whoever charges for minutes sets the numbers. Zero is a real limit, and it refuses everything.
@dataclass(frozen=True)
class Quotas:
    """What an org may consume and keep: minutes, messages, agents, calls, facts, chunks, people."""

    minutes: int | None = None
    messages: int | None = None
    agents: int | None = None
    concurrent_calls: int | None = None
    memory_facts: int | None = None
    knowledge_chunks: int | None = None
    numbers: int | None = None
    seats: int | None = None
    llm_tokens: int | None = None
    # What the org's calls may cost in a calendar month, in whole euros, every world together: a
    # number a console shows beside what was spent. It is set with the quotas and is not one of
    # QUOTAS, because nothing is refused over it — no call, no push, no credits.exhausted; whoever
    # charges decides what reaching it means.
    budget_eur: int | None = None
    # Which of the box's vendor keys the org's calls may run on where it brought none of its own:
    # None lends every one (no row, a self-hosted box), an empty set none, a set those entries —
    # a vendor, or `vendor/model` by prefix. Not one of QUOTAS: nothing is counted against it.
    # What an entry means is providers/lent_keys.py's: types/ knows no vendor (test_isolation.py).
    lends: frozenset[str] | None = None

    def __post_init__(self) -> None:
        for name in QUOTAS:
            limit: int | None = getattr(self, name)
            if limit is not None and limit < 0:
                raise DeclarationRefused(f"a quota is a count, and {name} cannot be {limit}")
        if self.budget_eur is not None and self.budget_eur < 0:
            raise DeclarationRefused(f"a budget is euros, and cannot be {self.budget_eur}")

    # The three questions anybody asks a quota, so the NULL rule is written once and every door,
    # every gate and every lookup reads it off the same three lines.
    def reached(self, quota: QuotaName, used: float) -> int | None:
        """The limit, when this much is already at or past it: nothing more of it fits."""
        limit: int | None = getattr(self, quota)
        return limit if limit is not None and used >= limit else None

    # A push is one replacement whose size is known before a row is written, so it is judged
    # whole: it fits when what the org would then keep is not more than the limit.
    def exceeded(self, quota: QuotaName, keeping: float) -> int | None:
        """The limit, when keeping this much would be more than it: this much does not fit."""
        limit: int | None = getattr(self, quota)
        return limit if limit is not None and keeping > limit else None

    # Zero is not "nearly none": it is a plan that does not include the feature at all, and the
    # lookups read it to answer with nothing rather than paying an embedder to find nothing.
    def switched_off(self, quota: QuotaName) -> bool:
        """Whether the org may keep none of these at all, which is a feature it does not have."""
        limit: int | None = getattr(self, quota)
        return limit == 0


# The event a quota's refusal is written as: at a door by admission, and on a call's own log by the
# worker whose clock the org's minutes ran out on (session/voice/time_limit.py).
EXHAUSTED = "credits.exhausted"


# What the org's minutes leave one call, as admission answers it at the open (orgs/admission.py):
# the seconds, and the minutes quota they come out of — which a worker cutting the call names in
# the credits.exhausted it writes, so the log says the org's minutes ended it and not the agent.
@dataclass(frozen=True)
class Ceiling:
    """How long a call may last by the org's minutes, and the quota that says so."""

    seconds: int
    minutes: int


# How many of a quota's thing one org keeps right now, asked of the table that keeps them — a
# count of rows, never a counter column. The gate holds one of these and calls it ONLY when a
# limit is set: a count is a query over a whole table, and an org nobody limited never pays for it.
type Counting = Callable[[str], Awaitable[int]]

# One org's quotas, asked of whoever holds them — the table, or the gate that reads it — by a
# package that may not import orgs/ (lookups/) or does not hold the table (a text call's opening).
type QuotasOf = Callable[[str], Awaitable[Quotas]]


def parse_slug(slug: str) -> str:
    """The slug, if it is one. A door refuses a bad one with the sentence and not a 422."""
    return Org(id=slug, slug=slug, name=slug).slug

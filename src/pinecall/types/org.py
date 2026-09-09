"""Org: the tenant — whose agents, keys, routes, calls and usage these are — and its quotas."""

import re
from dataclasses import dataclass
from typing import Literal

from pinecall.types.refused import DeclarationRefused

# The org a box has when nobody has made a second one: `migrate up` issues the first key against
# it, the keys and routes verbs fall back to it, and the schema seeds it as the first row.
DEFAULT_ORG = "default"

# A slug is what an operator types and a URL carries: lowercase, digits, a dash between words. An
# id is minted from it and never changes; the slug may be renamed later, which is why there are two.
_A_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")

# What a quota is about. The names are the columns and the wire's, spelled once.
type QuotaName = Literal["minutes", "messages", "agents", "concurrent_calls"]
QUOTAS: tuple[QuotaName, ...] = ("minutes", "messages", "agents", "concurrent_calls")


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
    """What an org may consume: minutes of call, messages, agents held, calls at once."""

    minutes: int | None = None
    messages: int | None = None
    agents: int | None = None
    concurrent_calls: int | None = None

    def __post_init__(self) -> None:
        for name in QUOTAS:
            limit: int | None = getattr(self, name)
            if limit is not None and limit < 0:
                raise DeclarationRefused(f"a quota is a count, and {name} cannot be {limit}")


def a_slug(slug: str) -> str:
    """The slug, if it is one. A door refuses a bad one with the sentence and not a 422."""
    return Org(id=slug, slug=slug, name=slug).slug

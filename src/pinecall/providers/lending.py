"""What the box lends an org: which of its vendor keys, and for which models, a call may run on."""

from __future__ import annotations

from collections.abc import Iterable

from pinecall._exceptions import PinecallError
from pinecall.providers import catalog

# An entry is a vendor, which lends every model of it, or `vendor/model`, which lends that model
# only — read as a PREFIX, because an id carries a date: `anthropic/claude-haiku-4-5` lends
# `claude-haiku-4-5-20251001`, the prefix rule providers/prices.py prices a snapshot by. It never
# lends `claude-sonnet-5`, and `openai/gpt-5.4-mini` never lends `gpt-5.4`: a family's cheap end
# is what a free trial runs on, and the dear end is the tenant's own key or nothing.
MODEL_SEPARATOR = "/"

# The refusal, said before a call opens and when a person picks the model: what they asked for,
# what they may run on, and the one other way — their own key, which is never refused.
NOT_LENT = (
    "{what} is not lent to this org: it may run on {lent}, or on a key of its own "
    "(pinecall providers add {vendor})"
)
NOTHING = "nothing of the box's"
NO_SUCH_VENDOR = "{entry!r} lends no vendor this build catalogues (GET /v1/providers lists them)"
NO_MODEL = "{entry!r} names a vendor and no model after the {separator}"


class NotLent(PinecallError):
    """A vendor or a model the box does not lend this org, and the org brought no key for."""


def lent(lends: frozenset[str] | None, vendor: str, model: str | None) -> bool:
    """Whether the box's key may carry this vendor's model for an org lent `lends`. A model of
    None is the vendor's own default and nothing more is known: only a whole vendor lends it."""
    if lends is None:
        return True
    name = catalog.canonical(vendor)
    if name in lends:
        return True
    if model is None:
        return False
    prefix = f"{name}{MODEL_SEPARATOR}"
    return any(
        model.startswith(entry.removeprefix(prefix)) for entry in lends if entry.startswith(prefix)
    )


def refusal(lends: frozenset[str], vendor: str, model: str | None) -> str:
    """The sentence a door and a call both say when `lent` answered no."""
    name = catalog.canonical(vendor)
    what = name if model is None else f"{name}{MODEL_SEPARATOR}{model}"
    return NOT_LENT.format(what=what, lent=", ".join(sorted(lends)) or NOTHING, vendor=name)


def a_lending(entries: Iterable[str]) -> frozenset[str]:
    """The entries as a door takes them: each vendor catalogued and spelled once, each model named.
    NotLent names the first entry that is neither."""
    kept: set[str] = set()
    for entry in entries:
        vendor, separator, model = entry.strip().partition(MODEL_SEPARATOR)
        if catalog.named(vendor) is None:
            raise NotLent(NO_SUCH_VENDOR.format(entry=entry))
        if separator and not model.strip():
            raise NotLent(NO_MODEL.format(entry=entry, separator=MODEL_SEPARATOR))
        name = catalog.canonical(vendor)
        kept.add(f"{name}{MODEL_SEPARATOR}{model.strip()}" if separator else name)
    return frozenset(kept)

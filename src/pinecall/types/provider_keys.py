"""Whose key a call runs on: the keys one org brought, and which of the box's it may use instead."""

# WHICH vendors an org may bring a key for is not here and cannot be: that is read off
# providers/catalog.py, and types/ imports nothing of ours (tests/test_isolation.py). The door that
# refuses an unknown vendor asks the catalog — api/provider_keys.py, cli/orgs/verbs.py — so the list
# in the refusal is the same forty-odd names the pipeline can actually be built out of.

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

# One org's own keys, vendor by vendor. Empty is the common case: the box's env keys are used.
type ProviderKeys = Mapping[str, str]

# The org that brought none, spelled once: every door that reads keys falls back to this.
NO_ORG_KEYS: ProviderKeys = MappingProxyType({})


# What one org's calls may run on, in one value, because the two halves are never read apart: the
# keys it brought, which always win for their vendor, and what the box lends it for the rest. A
# lending entry is a vendor (`deepgram`: every model of it) or `vendor/model` (`anthropic/claude-
# haiku-4-5`: that model and its dated snapshots). None lends everything the box has a key for —
# what a self-hosted box and every org nobody limited run on — and an empty set lends nothing, so
# the org runs only on what it brought. The rule that reads it is providers/lending.py.
@dataclass(frozen=True)
class Brought:
    """The org's own keys, and what the box lends it beside them."""

    keys: ProviderKeys = NO_ORG_KEYS
    lends: frozenset[str] | None = None


# The org that brought nothing and was limited in nothing: the box's keys, for every vendor.
NOTHING_BROUGHT = Brought()

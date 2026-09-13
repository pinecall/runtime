"""Whose key a call runs on: what one org's own set of provider keys is, and what none is."""

# WHICH vendors an org may bring a key for is not here and cannot be: that is read off
# providers/catalog.py, and types/ imports nothing of ours (tests/test_isolation.py). The door that
# refuses an unknown vendor asks the catalog — api/provider_keys.py, cli/orgs/verbs.py — so the list
# in the refusal is the same forty-odd names the pipeline can actually be built out of.

from collections.abc import Mapping
from types import MappingProxyType

# One org's own keys, vendor by vendor. Empty is the common case: the box's env keys are used.
type ProviderKeys = Mapping[str, str]

# The org that brought none, spelled once: every door that reads keys falls back to this.
NO_ORG_KEYS: ProviderKeys = MappingProxyType({})

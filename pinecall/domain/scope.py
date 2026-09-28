"""A scope: an org, one of its two worlds, and the holder a request acts as."""

from dataclasses import dataclass

from pinecall.domain.names import PRODUCTION, Env

# Holder value for org-owned rows: an empty string, not NULL, because it is part of a primary
# key and NULL never matches.
THE_ORGS_OWN = ""

SCOPE_ATTRIBUTE = "pinecall.scope"


# What a request may see: an org, one of its two worlds, and in the sandbox one developer's own.
@dataclass(frozen=True)
class Scope:
    """An org's scope: the org, the environment and the holder ("" for the org itself)."""

    org: str
    env: Env = PRODUCTION
    holder: str = THE_ORGS_OWN

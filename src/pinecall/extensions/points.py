"""The points: what a policy may decide, said in the runtime's own mechanisms, never in plans."""

from __future__ import annotations

from collections.abc import Callable

from pinecall.types import Org, Quotas

# What a new org may do, decided by whoever charges for it. The runtime asks this once, at the
# alta, and writes the answer as the org's quotas in the same breath the org is made — so an org
# is never standing with the wrong limits. It speaks Quotas, the mechanism, and knows no word for
# a plan: a package that charges maps its plans onto these numbers on its own side.
type Admitting = Callable[[Org, str], Quotas]


def unlimited(org: Org, email: str) -> Quotas:  # noqa: ARG001 — the shape every policy has
    """A box of its own: an org made here may do everything, which is what no row means."""
    return Quotas()


# One object the gateway holds, with one slot per point and the runtime's own answer in each until
# an extension registers another. A package named by PINECALL_EXTENSIONS is imported at startup
# and handed this object to fill (loading.py); the doors read it and never learn who filled it.
class Extensions:
    """The points, each holding the policy that answers it."""

    def __init__(self, admitted: Admitting = unlimited) -> None:
        self.admitted = admitted

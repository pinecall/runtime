"""The points: what a policy may decide, said in the runtime's own mechanisms, never in plans."""

from __future__ import annotations

from collections.abc import Callable

from pinecall.types import Env, Org, Quotas

# What a new org may do, decided by whoever charges for it. An instance asks this whenever it
# makes an org it did not have — production at signup, a sandbox when it first mirrors one — and
# writes the answer as the org's quotas in the same breath the org is made, so an org is never
# standing with the wrong limits. The world is the instance's (`PINECALL_WORLD`): one package serves
# both, and what a new org may do in the sandbox is not what it may do in production. It speaks
# Quotas, the mechanism, and knows no word for a plan: a package that charges maps its plans onto
# these numbers on its own side. `already` is how many orgs the person already belongs to on this
# instance, the new one not counted: a policy that gives a trial gives it to a person's FIRST org,
# and a second org made by the same email is a thing only this number lets it tell apart.
type Admitting = Callable[[Org, str, Env, int], Quotas]


def unlimited_quotas(org: Org, email: str, world: Env, already: int) -> Quotas:  # noqa: ARG001 — the shape
    """A box of its own: an org made here may do everything, which is what no row means."""
    return Quotas()


# One object the gateway holds, with one slot per point and the runtime's own answer in each until
# an extension registers another. A package named by PINECALL_EXTENSIONS is imported at startup
# and handed this object to fill (loading.py); the doors read it and never learn who filled it.
class Extensions:
    """The points, each holding the policy that answers it."""

    def __init__(self, admitted: Admitting = unlimited_quotas) -> None:
        self.admitted = admitted

"""A call a gateway learns of from another: served here from what was kept when it opened."""

import time

from pinecall.gateway._served import Served, Serving, first_seen
from pinecall.log import openings, queries


# None for a call nobody claimed, one sealed, or one opened by a release that kept no opening.
async def known_here(serving: Serving, call: str) -> Served | None:
    """The call as this gateway serves it, first seen here from its opening when need be."""
    served = serving.live.calls.get(call)
    if served is not None:
        return served
    pool = serving.connections.pool
    kept = await queries.scope_of_call(pool, call)
    if kept is None or kept.scope is None or kept.sealed:
        return None
    opening = await openings.opening_of(pool, call)
    if opening is None:
        return None
    return first_seen(serving, opening.context, opening.config, kept.scope, time.monotonic())

"""Which agent answers a WhatsApp number, when the two tables that could say so disagree."""

from __future__ import annotations

import logging

from pinecall.routes.answering import Declaring
from pinecall.routes.table import Routes
from pinecall.types import Channel, Route

logger = logging.getLogger(__name__)

# The channel this whole package is about, written once so a lookup and a Route are never spelled
# two different ways.
WHATSAPP: Channel = "whatsapp"

# Nobody answers there. An operator sees the number they have to type, and Meta still gets its 200.
NO_ROUTE = (
    "whatsapp: nothing answers at %s — add it with "
    "`pinecall-runtime routes add <org> %s <agent> --channel whatsapp`"
)


# The same rule GET /v1/routes reads the two tables under, asked here from the other side: an
# inbound message knows its number and nothing else. The operator's row outranks a declaration,
# because `routes add` moving a number with no deploy is the whole point of the verb.
# See docs/decisions/routes.md.
async def answering(routes: Routes, registry: Declaring, number: str) -> Route | None:
    """Who takes a message at this number: the operator's row first, the app's declaration after."""
    typed = await routes.at(WHATSAPP, number)
    if typed is not None:
        return typed
    declared = registry.at(WHATSAPP, number)
    # By the DOOR and never by the number alone: Clínica Norte declares its phone and its WhatsApp
    # on the same number, and the phone route came first in its list — a WhatsApp call opened
    # through it was refused by the contract on the first signed body a live gateway ever received.
    at_that_number = (
        next((route for route in declared.routes if route.door == (WHATSAPP, number)), None)
        if declared is not None
        else None
    )
    if at_that_number is None:
        logger.warning(NO_ROUTE, number, number)
    return at_that_number

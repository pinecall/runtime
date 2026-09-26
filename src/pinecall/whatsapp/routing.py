"""Which agent answers a WhatsApp number: the row an operator typed, and there is no other."""

from __future__ import annotations

import logging

from pinecall.routes.records import Routes
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


# A door is a row somebody typed and nothing else: a class declares no doors, so there is no
# second table to disagree with this one and no precedence to work out (docs/decisions/routes.md).
async def answering(routes: Routes, number: str) -> Route | None:
    """Who takes a message at this number, or None and the line that says what to type."""
    typed = await routes.at(WHATSAPP, number)
    if typed is None:
        logger.warning(NO_ROUTE, number, number)
    return typed

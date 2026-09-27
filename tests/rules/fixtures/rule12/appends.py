"""Fixture: an event type the protocol does not know."""


async def write(log: object) -> None:
    """Append."""
    await log.append("call.imagined", {})
    await log.append("call.started", {})

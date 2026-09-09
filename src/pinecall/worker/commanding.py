"""The app's commands for one call: read from the gateway, applied to the bridge."""

from __future__ import annotations

import logging
from typing import Protocol

from pinecall._exceptions import PinecallError
from pinecall.log import REFUSED
from pinecall.worker.client import Gateway, GatewayRefused
from pinecall_protocol import Command, encode
from pinecall_protocol.events import ErrorEvent

logger = logging.getLogger(__name__)

# A command this call cannot run is the app's mistake, not the end of the call: it goes into the
# log as an error, which is where the app that sent it is already reading, and the caller hears
# nothing. The text channel answers the very same refusal down the app's socket.


class Applying(Protocol):
    """What this loop needs of the bridge, and nothing else it holds."""

    async def apply(self, command: Command) -> None:
        """One protocol command onto this call: the session, the prompt, or the ending."""
        ...


# One stream per call, opened once the bridge exists so that the first prompt.set has somewhere to
# land, and read in order: the app's commands are applied in the order the app sent them.
async def served(gateway: Gateway, bridge: Applying, call: str) -> None:
    """Every command the app sends for this call, until the gateway seals it or lets go."""
    try:
        async for command in gateway.commands(call):
            await _applied(gateway, bridge, call, command)
    except GatewayRefused as unreachable:
        logger.warning("call %s: no commands will arrive (%s)", call, unreachable)


async def _applied(gateway: Gateway, bridge: Applying, call: str, command: Command) -> None:
    """One command onto the call, with any refusal written where the app can read it."""
    try:
        await bridge.apply(command)
    except PinecallError as refused:
        await _refused(gateway, call, command, str(refused))


async def _refused(gateway: Gateway, call: str, command: Command, why: str) -> None:
    """Say no in the protocol's own words, naming the command and the id the app gave it."""
    said = ErrorEvent(
        code=REFUSED, message=why, command=command.type, id=command.id, recoverable=True
    )
    try:
        await gateway.append(call, "error", encode(said))
    except GatewayRefused as unreachable:
        logger.warning("call %s: %s, and the log did not take it (%s)", call, why, unreachable)

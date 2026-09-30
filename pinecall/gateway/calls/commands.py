"""The app's and the desk's commands for a call, told to whichever gateway runs it."""

from pinecall.process.signal import Signal
from pinecall.wire.frames import Command

# One channel per call: every gateway that serves the call listens on it while it does.
COMMANDS_CHANNEL = "cmd:{call}"

# The one verb a desk sends, which a written call's session takes as a supervisor's.
SUPERVISOR_VERB = "supervisor.verb"


# Lossy, as today's queue is on a gateway that dies: nobody hearing it is the refusal the sender
# gives, and a Redis that is away is a NotAvailable the app can try again after.
async def commanded(signal: Signal, command: Command) -> bool:
    """Tell the gateways that serve the call; False when none does."""
    channel = COMMANDS_CHANNEL.format(call=command.call)
    return await signal.published(channel, command.model_dump_json().encode()) > 0

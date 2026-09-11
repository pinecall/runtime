"""The first entries of a call, built from its context: ringing or dialing, then started."""

from __future__ import annotations

from pinecall.types import CallContext
from pinecall_protocol import WireModel, defs
from pinecall_protocol.events import CallDialing, CallRinging, CallStarted


# An inbound call was offered to an agent and an outbound one is being placed; the two are
# different facts and the protocol gives each its own first entry. `from` is a keyword, so both
# are built from the wire's own key names.
def arrived(context: CallContext, to: str) -> tuple[str, WireModel]:
    """The first entry of a call, told by the direction it came from."""
    said = {
        "channel": context.channel,
        "from": context.caller,
        "to": to,
        "run": context.run,
        "caller": None,
    }
    if context.direction == "outbound":
        return "call.dialing", CallDialing.model_validate(said)
    door = defs.Route(channel=context.route.channel, number=context.route.number)
    return "call.ringing", CallRinging.model_validate({**said, "route": door})


def started(context: CallContext, to: str, at: float) -> CallStarted:
    """Media is up: the entry every word of the call comes after."""
    return CallStarted.model_validate(
        {
            "channel": context.channel,
            "direction": context.direction,
            "from": context.caller,
            "to": to,
            "run": context.run,
            "caller": None,
            "started_at": at,
            "env": context.env,
        }
    )

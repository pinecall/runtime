"""The first entries of a call, built from its context: ringing or dialing, then started."""

from __future__ import annotations

from pinecall.types import CallContext
from pinecall_protocol import WireModel, defs
from pinecall_protocol.events import CallDialing, CallRinging, CallStarted


# Which end is which, and it is not the same question in both directions. On a call that RANG,
# `caller` is the far end and it is where the call came FROM. On one this box PLACED, the far end
# is still the caller — memory files the call under it, the router carries it — but the call went
# TO it, and the number shown was the org's own door. Writing `from: caller` for both would have
# put our own number in the `to` of every outbound log and the customer's in the `from`.
def two_ends(context: CallContext, door: str) -> tuple[str, str]:
    """Where this call came from and where it went, by the direction it took."""
    if context.direction == "outbound":
        return door, context.caller
    return context.caller, door


# An inbound call was offered to an agent and an outbound one is being placed; the two are
# different facts and the protocol gives each its own first entry. `from` is a keyword, so both
# are built from the wire's own key names.
def arrived(context: CallContext, door: str, asked_by: str | None = None) -> tuple[str, WireModel]:
    """The first entry of a call, told by the direction it came from."""
    from_, to = two_ends(context, door)
    said = {"channel": context.channel, "from": from_, "to": to, "run": context.run, "caller": None}
    if context.direction == "outbound":
        # Who asked is on the call's own first entry and nowhere else in the log: a bill for four
        # hundred calls overnight is read backwards from here, and the org's log is where a tenant
        # looks first. A ring is asked for by nobody, so an inbound entry carries none.
        return "call.dialing", CallDialing.model_validate({**said, "asked_by": asked_by})
    route = defs.Route(channel=context.route.channel, number=context.route.number)
    return "call.ringing", CallRinging.model_validate({**said, "route": route})


def started(context: CallContext, door: str, at: float) -> CallStarted:
    """Media is up: the entry every word of the call comes after."""
    from_, to = two_ends(context, door)
    return CallStarted.model_validate(
        {
            "channel": context.channel,
            "direction": context.direction,
            "from": from_,
            "to": to,
            "run": context.run,
            # Who is being played on this call, when a simulation opened it. The log is where the
            # fact lives; call_facts is a projection of this line (log/call_facts.py, migration
            # 0046). And that caller's own rule for the call, which the `persona` judge reads from
            # HERE at hang-up: the log is the truth about what the call was made under.
            "persona": context.persona,
            "accepts_when": context.accepts_when,
            "declines_when": context.declines_when,
            "caller": None,
            "started_at": at,
            "env": context.env,
        }
    )

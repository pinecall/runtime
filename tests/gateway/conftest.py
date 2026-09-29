"""What the gateway's own tests share: the agent they serve, its scope, and a call of it."""

from datetime import date

from pinecall.domain.call import CallContext, Route, new_call_id
from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log.logs import started_entry

AGENT = "agenda"
OURS = Scope("org_a", "sandbox")


def a_call(scope: Scope = OURS, channel: str = "web") -> CallContext:
    """A call of the agent in the scope."""
    route = Route(
        org=scope.org,
        agent=AGENT,
        channel="phone" if channel == "phone" else "web",
        number="+59829001199" if channel == "phone" else None,
        env=scope.env,
    )
    return CallContext(
        call=new_call_id(),
        channel=route.channel,
        direction="inbound",
        caller="+59899123456",
        route=route,
        today=date(2026, 9, 28),
    )


def a_start(context: CallContext) -> JsonObject:
    """The call.started a worker would write for the call."""
    return started_entry(context, context.route.number or AGENT, 1.0)

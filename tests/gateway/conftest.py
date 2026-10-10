"""What the gateway's own tests share: the agent they serve, its scope, a call, a judge model."""

from datetime import date

from pinecall.domain.call import CallContext, Route, new_call_id
from pinecall.domain.names import JsonObject
from pinecall.domain.scope import Scope
from pinecall.log.logs import started_entry
from pinecall.providers.catalog import Judge, Providers
from tests.conftest import configured

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
    return started_entry(context, context.route.number or AGENT, 1.0, medium="voice")


def judging(*answers: tuple[str, JsonObject]) -> Providers:
    """The box with a judge model on acme answering each request with the next tool call."""
    replies: list[list[str | dict[str, object]]] = [
        [{"name": tool, "arguments": arguments}] for tool, arguments in answers
    ]
    return configured(replies).model_copy(
        update={"judge": Judge.model_validate({"llm": {"vendor": "acme"}, "ceiling_usd": 0.01})}
    )


def a_hold(reason: str = "fine") -> tuple[str, JsonObject]:
    """A verdict judge's answer that the call held."""
    return ("submit_verdict", {"verdict": "held", "reason": reason, "positions": []})


def broken(reason: str, *positions: int) -> tuple[str, JsonObject]:
    """A verdict judge's answer that the call broke, at these positions."""
    return ("submit_verdict", {"verdict": "broken", "reason": reason, "positions": [*positions]})


# A trigger that says the judge does not apply: it is N/A, and its question is never asked.
NOT_APPLIES: tuple[str, JsonObject] = (
    "submit_applies",
    {"applies": False, "reason": "nothing of the kind"},
)


# The library on an inbound call, in its order: disclosed, ended-well and grounded are asked;
# honoured-stop and promises ask their trigger first; consent and identified are gated N/A.
EVERY_HELD = (a_hold(), a_hold(), a_hold(), NOT_APPLIES, NOT_APPLIES)

"""Tests for a call served here as a door reaches it: the socket it goes to, its tools' answers."""

import asyncio

from pinecall.domain.agent import AgentConfig
from pinecall.domain.scope import Scope
from pinecall.gateway._gateway import Gateway
from pinecall.gateway._sockets import Sockets
from pinecall.gateway.calls.serving import served_call, serving_agent
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.session.call import ToolUse
from pinecall.wire.parts import ToolResult
from tests.conftest import postgres
from tests.gateway.conftest import AGENT, OURS, a_call
from tests.session.test_tools import went_out

ANA = Scope("org_a", "sandbox", "m_ana")
BEN = Scope("org_a", "sandbox", "m_ben")


# a_call's caller rings from +59899123456: Ben's own phone here.
@postgres
async def test_a_call_that_rang_reaches_its_phones_owner_and_one_opened_with_a_key_its_scope(
    store: Store,
) -> None:
    sockets = Sockets(Logs(store))
    await sockets.register("app_ana", ANA, AGENT, sdk=None, takes_unclaimed=True)
    await sockets.register("app_ben", BEN, AGENT, sdk=None, takes_unclaimed=True)
    sockets.calls_from("sandbox", "+59899123456", "m_ben")
    rang = serving_agent(sockets, ANA, AGENT, None, a_call(ANA, channel="phone"))
    opened = serving_agent(sockets, ANA, AGENT, None, a_call(ANA))
    assert rang is not None
    assert opened is not None
    assert (rang.owner, opened.owner) == ("app_ben", "app_ana")


# A gateway that restarted serves the call again from the worker's word, with nothing in memory:
# the tool the worker asks again is answered from the log, never sent to the app a second time.
@postgres
async def test_a_tool_that_finished_before_a_restart_is_answered_from_the_log(
    wired: Gateway,
) -> None:
    context = a_call()
    config = AgentConfig(slug=AGENT)
    served = served_call(wired.serving, None, context, config, OURS)
    use = ToolUse("t1", "book", {})
    out = went_out(served.log)
    first = asyncio.create_task(served.tools.ran(use, None))
    await asyncio.wait_for(out.wait(), 5)
    assert served.tools.answered(ToolResult(call_id="t1", name="book", output="booked"))
    await first
    wired.live.close(context.call)
    wired.logs.forget(context.call)
    again = served_call(wired.serving, None, context, config, OURS)
    assert await again.tools.ran(use, None) == await first
    kinds = [entry.type for entry in await wired.logs.store.whole(context.call)]
    assert kinds == ["tool.call", "tool.result"]

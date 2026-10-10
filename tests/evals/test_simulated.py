"""Tests for a persona on a written line: its lines said as the agent answers, ended last."""

import pytest

from pinecall.domain.errors import UpstreamFailed
from pinecall.domain.scope import Scope
from pinecall.evals import simulated
from pinecall.gateway._text_calls import open_text
from pinecall.providers import catalog
from pinecall.session.session import Session
from tests.conftest import AGENT, Knocking, configured, postgres
from tests.gateway.api.conftest import a_call, an_app


async def a_written_call(knocking: Knocking) -> Session:
    """A written call to the agent the org's app holds, unstarted, as the simulation opens one."""
    context = a_call(knocking, channel="web")
    scope = Scope(context.route.org, context.route.env)
    registration = knocking.gateway.sockets.serving(scope, AGENT, None)
    assert registration is not None
    return await open_text(knocking.gateway.serving, registration, context)


@postgres
async def test_each_line_is_said_once_the_agent_answered_and_the_caller_hangs_up_last(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    await catalog.configure(pool, configured([["el martes a las diez"], ["de nada"]]))
    app = await an_app(knocking)
    session = await a_written_call(knocking)
    lines = iter([("quiero un turno", False), ("gracias", True)])

    async def next_line(_turns_left: int) -> tuple[str, bool]:
        return next(lines)

    count = await simulated.converse(session, knocking.gateway.logs, next_line, 5)
    entries = await knocking.gateway.logs.store.whole(session.call.context.call)
    heard = [entry.data["text"] for entry in entries if entry.type == "turn.user"]
    ended = next(entry.data for entry in entries if entry.type == "call.ended")
    assert (count, heard) == (2, ["quiero un turno", "gracias"])
    assert (ended["reason"], ended["ended_by"]) == ("caller_hung_up", "caller")
    await app.close()


@postgres
async def test_a_caller_whose_model_broke_ends_the_call_as_broken_and_says_so(
    knocking: Knocking,
) -> None:
    await catalog.configure(knocking.gateway.connections.pool, configured())
    app = await an_app(knocking)
    session = await a_written_call(knocking)

    async def broke(_turns_left: int) -> tuple[str, bool]:
        raise UpstreamFailed("the model playing the caller said nothing")

    with pytest.raises(UpstreamFailed, match="said nothing"):
        await simulated.converse(session, knocking.gateway.logs, broke, 3)
    entries = await knocking.gateway.logs.store.whole(session.call.context.call)
    ended = next(entry.data for entry in entries if entry.type == "call.ended")
    assert (ended["reason"], ended["ended_by"]) == ("error", "platform")
    await app.close()

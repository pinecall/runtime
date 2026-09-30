"""Tests for a call's prompt read back: the knowledge its settings placed and the app's blocks."""

from pinecall.domain.agent import block_hash
from pinecall.domain.names import JsonObject
from pinecall.tenancy import prompts
from pinecall.wire.rest.calls import OpenCallRequest
from tests.conftest import AGENT, Knocking, postgres, received_until, sent
from tests.gateway.api.conftest import a_call, an_app

KNOWLEDGE = "# Precios\nLa consulta cuesta 45 euros."

VIEW = "Hoy atiende la doctora Vidal."

READ = "SELECT count(*) AS reads FROM reads WHERE subject = %(call)s AND what = 'log'"


def changed(name: str, text: str) -> JsonObject:
    """The entry a session writes when a block of its prompt changes: its hash, never its text."""
    return {
        "type": "prompt.changed",
        "data": {"name": name, "hash": block_hash(text), "chars": len(text)},
    }


@postgres
async def test_a_calls_prompt_comes_back_block_by_block_with_the_words_kept(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as org:
        await org.put(f"/v1/agents/{AGENT}/settings", json={"config": {"knowledge": KNOWLEDGE}})
    context = a_call(knocking)
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        await worker.post(f"/v1/calls/{context.call}/events", json=changed("knowledge", KNOWLEDGE))
    await sent(app, "prompt.set", {"name": "view", "text": VIEW}, call="CA_not_served_here")
    await received_until(app, "error")
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post(f"/v1/calls/{context.call}/events", json=changed("view", VIEW))
        await worker.post(f"/v1/calls/{context.call}/events", json=changed("view", ""))
    async with knocking.http(knocking.app["sandbox"]) as org:
        answer = await org.get(f"/v1/calls/{context.call}/prompt")
        nobody = await org.get("/v1/calls/CA_nobody/prompt")
    async with knocking.http(knocking.app["production"]) as other_world:
        unseen = await other_world.get(f"/v1/calls/{context.call}/prompt")
    assert answer.status_code == 200, answer.text
    blocks = [(row["name"], row["chars"], row["text"]) for row in answer.json()["blocks"]]
    assert blocks == [
        ("knowledge", len(KNOWLEDGE), KNOWLEDGE),
        ("view", len(VIEW), VIEW),
        ("view", 0, ""),
    ]
    assert (nobody.status_code, unseen.status_code) == (404, 404)
    pool = knocking.gateway.connections.pool
    async with pool.connection() as connection:
        row = await (await connection.execute(READ, {"call": context.call})).fetchone()
    assert row is not None
    assert row["reads"] == 1, "reading a call's prompt is a read of the call, on the record"
    assert await prompts.texts_of(pool, "another-org", [block_hash(VIEW)]) == {}
    await app.close()

"""Tests for the entries socket: a worker's batches down one socket, answered with their seqs."""

import asyncio
import json

from websockets.asyncio.client import ClientConnection

from pinecall.domain.names import JsonObject
from pinecall.fleet.client import gateway_at
from pinecall.gateway._deps import POLICY_VIOLATION
from pinecall.wire.rest.calls import (
    AppendEntriesRefused,
    AppendEntriesResponse,
    OpenCallRequest,
)
from tests.conftest import AGENT, Knocking, postgres
from tests.gateway.api.conftest import a_call


def a_batch(after: int, *names: str) -> str:
    """A frame of custom entries, after how many the log took."""
    entries: list[JsonObject] = [
        {"type": "custom", "data": {"name": name, "data": {}}, "ts": 1.5} for name in names
    ]
    return json.dumps({"after": after, "entries": entries})


async def answered(socket: ClientConnection) -> AppendEntriesResponse | AppendEntriesRefused:
    """The next frame the socket is sent: a batch's seqs, or its refusal."""
    raw: JsonObject = json.loads(await asyncio.wait_for(socket.recv(), 5))
    if "refused" in raw:
        return AppendEntriesRefused.model_validate(raw)
    return AppendEntriesResponse.model_validate(raw)


async def landed(socket: ClientConnection) -> AppendEntriesResponse:
    """The next frame, which must be a batch's seqs."""
    frame = await answered(socket)
    assert isinstance(frame, AppendEntriesResponse)
    return frame


async def refused_with(socket: ClientConnection) -> AppendEntriesRefused:
    """The next frame, which must be a refusal."""
    frame = await answered(socket)
    assert isinstance(frame, AppendEntriesRefused)
    return frame


async def an_open_call(knocking: Knocking) -> str:
    """A sandbox call opened by the fleet, as a worker opens one."""
    client = gateway_at(knocking.url, knocking.fleet["sandbox"])
    context = a_call(knocking)
    await client.open(OpenCallRequest(agent=AGENT, context=context))
    await client.aclose()
    return context.call


@postgres
async def test_batches_down_the_socket_land_once_in_order_and_a_retry_gets_the_same_seqs(
    knocking: Knocking,
) -> None:
    call = await an_open_call(knocking)
    socket = await knocking.socket(f"/v1/calls/{call}/entries", knocking.fleet["sandbox"])
    await socket.send(a_batch(0, "uno", "dos"))
    first = await landed(socket)
    await socket.send(a_batch(0, "uno", "dos"))
    again = await landed(socket)
    await socket.send(a_batch(2, "tres"))
    third = await landed(socket)
    await socket.close()
    seqs = [entry.seq for entry in first.entries]
    assert again == first, "a retry of the last batch is answered with the seqs it was given"
    assert [entry.seq for entry in third.entries] == [seqs[-1] + 1]
    kept = await knocking.gateway.logs.store.whole(call)
    assert [entry.data["name"] for entry in kept if entry.type == "custom"] == [
        "uno",
        "dos",
        "tres",
    ]
    assert await knocking.gateway.logs.store.written(call) == 3


@postgres
async def test_a_refused_batch_is_a_frame_and_the_socket_stays_for_the_next(
    knocking: Knocking,
) -> None:
    call = await an_open_call(knocking)
    socket = await knocking.socket(f"/v1/calls/{call}/entries", knocking.fleet["sandbox"])
    await socket.send(
        json.dumps({"after": 0, "entries": [{"type": "no.such", "data": {}, "ts": 1.5}]})
    )
    refused = await refused_with(socket)
    await socket.send(a_batch(0, "uno"))
    taken = await landed(socket)
    await socket.close()
    assert refused.status == 400
    assert "no event is called 'no.such'" in refused.refused
    assert len(taken.entries) == 1


@postgres
async def test_a_socket_with_no_key_or_for_a_call_nobody_opened_says_why_and_closes(
    knocking: Knocking,
) -> None:
    nobody = await knocking.socket("/v1/calls/call_x/entries", "pc_test_nobody")
    refused = await refused_with(nobody)
    await nobody.wait_closed()
    assert (refused.status, nobody.close_code) == (401, POLICY_VIOLATION)
    unopened = await knocking.socket("/v1/calls/call_x/entries", knocking.fleet["sandbox"])
    not_open = await refused_with(unopened)
    await unopened.wait_closed()
    assert (not_open.status, unopened.close_code) == (404, POLICY_VIOLATION)

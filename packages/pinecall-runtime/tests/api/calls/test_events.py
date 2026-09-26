"""GET /v1/calls/{id}/events as a reader sees it: the page, the stream, the cursor and the end."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

import pytest
from starlette.testclient import TestClient

from pinecall.api.calls.log_sink import is_sealed, sse
from pinecall.api.sse import RETRY_MS, SSE
from pinecall.auth.scopes import Reader
from pinecall.log.entry import Entry
from pinecall.log.filters import Filter
from pinecall.log.store import MemoryStore
from pinecall.log.writers import Logs
from pinecall.types.json import JsonObject
from pinecall_protocol import encode
from tests.api.conftest import A_KEY, A_READER

pytestmark = pytest.mark.unit

CALL = "call_the_one_being_read"
BEARER = {"Authorization": f"Bearer {A_KEY}"}
STREAMING = {**BEARER, "Accept": SSE}


async def a_call(logs: Logs, *types: str) -> None:
    """One call written the way a session writes it: an entry per type, in order."""
    log = logs.writing(CALL, "clara")
    for type in types:
        await log.append(type, {})


@dataclass(frozen=True)
class Answer:
    """What a reader got back: a status, and a body that is a JSON page or an SSE stream."""

    status: int
    body: str

    @property
    def page(self) -> Any:
        """The JSON flavour, parsed."""
        return json.loads(self.body)

    @property
    def sent(self) -> list[dict[str, str]]:
        """The SSE flavour, as the fields a browser would parse."""
        return frames(self.body)


# starlette's TestClient types its requests through httpx's private `_types`, which no checker can
# resolve, so every request goes through here: two ignores in one place, and the tests below read
# an Answer whose fields are a status and a body.
def read(gateway: TestClient, url: str, headers: dict[str, str] | None = None) -> Answer:
    """One GET as a reader makes it: the status and the body, whatever the flavour."""
    got: Any = gateway.get(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
        url, headers=headers
    )
    return Answer(
        int(got.status_code),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        str(got.text),  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    )


def frames(body: str) -> list[dict[str, str]]:
    """The SSE body as the fields a browser would parse: one dict per frame, comments dropped."""
    parsed: list[dict[str, str]] = []
    for block in body.split("\n\n"):
        fields = dict(
            line.split(": ", 1) for line in block.splitlines() if line and not line.startswith(":")
        )
        # Only the entries: `retry` is a block of its own and a ping has no fields at all.
        if "event" in fields:
            parsed.append(fields)
    return parsed


# ── the door ────────────────────────────────────────────────────────────────────


def test_a_reader_with_no_key_is_refused(gateway: TestClient) -> None:
    assert read(gateway, f"/v1/calls/{CALL}/events").status == 401


def test_a_key_nobody_issued_is_refused(gateway: TestClient) -> None:
    refused = read(gateway, f"/v1/calls/{CALL}/events", {"Authorization": "Bearer nope"})
    assert refused.status == 401


# EventSource cannot set a header, so the query string is the browser's only way in — and what may
# travel there is a ROOM token, which is short-lived and reads one call. A key is the whole tenant
# for as long as nobody revokes it, and a URL is written down: the access log, the referrer, the
# history. This door took a key in `?token=` until 2026-09-20, against what its own paragraph said.
def test_a_room_token_may_travel_in_the_query_string(gateway: TestClient) -> None:
    from pinecall.auth.scopes import mint_room_token
    from tests.api.conftest import A_LIVEKIT

    token = mint_room_token(CALL, "participate", 4102444800.0, A_LIVEKIT)
    assert read(gateway, f"/v1/calls/{CALL}/events?token={token}").status == 200


def test_a_key_in_the_query_string_is_refused(gateway: TestClient) -> None:
    assert read(gateway, f"/v1/calls/{CALL}/events?token={A_KEY}").status == 401
    assert read(gateway, f"/v1/calls/{CALL}/state?token={A_KEY}").status == 401
    # The same key on the header is the way in, and still is.
    assert (
        read(gateway, f"/v1/calls/{CALL}/events", {"Authorization": f"Bearer {A_KEY}"}).status
        == 200
    )


# ── the JSON page ───────────────────────────────────────────────────────────────


async def test_a_page_carries_the_entries_above_the_cursor_and_where_to_resume(
    gateway: TestClient, logs: Logs
) -> None:
    await a_call(logs, "call.started", "user.said", "agent.said")
    page: Any = read(gateway, f"/v1/calls/{CALL}/events?after=1", BEARER).page
    assert [entry["seq"] for entry in page["entries"]] == [2, 3]
    assert page["next"] == 3
    assert page["live"] is True


async def test_a_limit_stops_the_page_and_next_says_where_it_stopped(
    gateway: TestClient, logs: Logs
) -> None:
    await a_call(logs, "call.started", "user.said", "agent.said")
    page: Any = read(gateway, f"/v1/calls/{CALL}/events?limit=2", BEARER).page
    assert [entry["seq"] for entry in page["entries"]] == [1, 2]
    assert page["next"] == 2


async def test_an_empty_page_says_there_is_nowhere_to_resume_from(
    gateway: TestClient, logs: Logs
) -> None:
    await a_call(logs, "call.started")
    page: Any = read(gateway, f"/v1/calls/{CALL}/events?after=1", BEARER).page
    assert page == {"entries": [], "live": True, "next": None}


async def test_a_sealed_call_reads_as_not_live(gateway: TestClient, logs: Logs) -> None:
    await a_call(logs, "call.started", "call.ended", "call.score")
    page: Any = read(gateway, f"/v1/calls/{CALL}/events", BEARER).page
    assert page["live"] is False


# ── the filters, at the sink ────────────────────────────────────────────────────


async def test_types_narrows_the_page_and_never_renumbers_what_survives(
    gateway: TestClient, logs: Logs
) -> None:
    await a_call(logs, "call.started", "user.said", "agent.said", "user.said")
    page: Any = read(gateway, f"/v1/calls/{CALL}/events?types=user.said", BEARER).page
    assert [entry["seq"] for entry in page["entries"]] == [2, 4]
    assert [entry["type"] for entry in page["entries"]] == ["user.said", "user.said"]


# A page whose every entry was filtered out still moves the cursor: next is what was READ.
async def test_a_page_that_kept_nothing_still_says_where_to_resume(
    gateway: TestClient, logs: Logs
) -> None:
    await a_call(logs, "call.started", "user.said")
    page: Any = read(gateway, f"/v1/calls/{CALL}/events?types=agent.said", BEARER).page
    assert page["entries"] == []
    assert page["next"] == 2


async def test_durable_drops_what_a_store_may_forget(gateway: TestClient, logs: Logs) -> None:
    log = logs.writing(CALL, "clara")
    await log.append("call.started", {})
    await log.append("agent.transcript", {"text": "hola", "speech_id": "s1", "final": False})
    page: Any = read(gateway, f"/v1/calls/{CALL}/events?durable=1", BEARER).page
    assert [entry["type"] for entry in page["entries"]] == ["call.started"]


def test_a_filter_nobody_could_mean_is_refused_by_name(gateway: TestClient) -> None:
    refused = read(gateway, f"/v1/calls/{CALL}/events?types=DROP TABLE", BEARER)
    assert refused.status == 400
    assert "lowercase words" in refused.page["detail"]


# ── the stream ──────────────────────────────────────────────────────────────────


async def test_the_stream_carries_retry_the_backlog_and_the_caught_up_marker(
    gateway: TestClient, logs: Logs
) -> None:
    await a_call(logs, "call.started", "call.ended", "call.score")
    body = read(gateway, f"/v1/calls/{CALL}/events", STREAMING).body
    assert body.startswith(f"retry: {RETRY_MS}\n\n")
    sent = frames(body)
    assert [frame["event"] for frame in sent] == [
        "call.started",
        "call.ended",
        "call.score",
    ]
    assert [frame["id"] for frame in sent] == ["1", "2", "3"]
    assert json.loads(sent[0]["data"])["call"] == CALL


async def test_last_event_id_resumes_above_the_cursor(gateway: TestClient, logs: Logs) -> None:
    await a_call(logs, "call.started", "user.said", "call.score")
    body = read(gateway, f"/v1/calls/{CALL}/events", {**STREAMING, "Last-Event-ID": "2"}).body
    assert [frame["id"] for frame in frames(body)] == ["3"]


async def test_the_higher_of_after_and_last_event_id_wins(gateway: TestClient, logs: Logs) -> None:
    await a_call(logs, "call.started", "user.said", "call.score")
    body = read(
        gateway, f"/v1/calls/{CALL}/events?after=2", {**STREAMING, "Last-Event-ID": "1"}
    ).body
    assert [frame["id"] for frame in frames(body)] == ["3"]


async def test_the_body_ends_after_the_call_score(gateway: TestClient, logs: Logs) -> None:
    await a_call(logs, "call.started", "call.score")
    body = read(gateway, f"/v1/calls/{CALL}/events", STREAMING).body
    assert [frame["event"] for frame in frames(body)] == ["call.started", "call.score"]


# ── nothing more will ever be true of this call ─────────────────────────────────


async def test_a_sealed_log_read_from_its_end_is_nothing_more(
    gateway: TestClient, logs: Logs
) -> None:
    await a_call(logs, "call.started", "call.score")
    assert read(gateway, f"/v1/calls/{CALL}/events?after=2", BEARER).status == 204
    assert read(gateway, f"/v1/calls/{CALL}/events?after=2", STREAMING).status == 204


async def test_a_sealed_log_read_from_before_its_end_still_answers(
    gateway: TestClient, logs: Logs
) -> None:
    await a_call(logs, "call.started", "call.score")
    assert read(gateway, f"/v1/calls/{CALL}/events?after=1", BEARER).status == 200


async def test_a_call_nobody_wrote_is_not_over_it_is_empty(
    gateway: TestClient, store: MemoryStore
) -> None:
    assert await is_sealed(store, "call_nobody_made") is False
    assert read(gateway, "/v1/calls/call_nobody_made/events", BEARER).status == 200


# ── live, at the sink ───────────────────────────────────────────────────────────


# The HTTP tests above read a finished call, because a TestClient's loop is not this test's. The
# live half is the sink itself: an entry appended while the body is open reaches the reader.
async def test_an_entry_appended_while_the_stream_is_open_arrives(store: MemoryStore) -> None:
    logs = Logs(store)
    log = logs.writing(CALL, "clara")
    await log.append("call.started", {})
    body = cast(
        "AsyncIterator[str]", sse(log.stream(), as_written, A_READER, asyncio.Event()).body_iterator
    )
    assert await anext(body) == f"retry: {RETRY_MS}\n\n"
    assert frames(str(await anext(body)))[0]["event"] == "call.started"
    # The marker stands at the seq of the last entry it speaks for, and no store ever saw it.
    marker = frames(str(await anext(body)))[0]
    assert marker["event"] == "log.caught_up"
    assert marker["id"] == "1"
    assert json.loads(marker["data"])["data"] == {"seq": 1}
    assert [entry.type for entry in await store.since(CALL)] == ["call.started"]
    await log.append("user.said", {"text": "hola"})
    assert frames(str(await anext(body)))[0]["event"] == "user.said"


async def test_the_stream_of_an_agents_own_log_says_caught_up_too(store: MemoryStore) -> None:
    logs = Logs(store)
    log = logs.writing_agent("clara")
    await log.append("agent.registered", {"routes": [], "sdk": None})
    read = [entry async for entry in _first(log.stream(filter=Filter()), 2)]
    assert [entry.type for entry in read] == ["agent.registered", "log.caught_up"]
    assert read[-1].call is None


def as_written(entry: Entry, _reader: Reader) -> JsonObject:
    """The whole entry, projected by nobody: what sse() is handed when the sink is under test."""
    return encode(entry)


async def _first(entries: AsyncIterator[Entry], many: int) -> AsyncIterator[Entry]:
    """The first `many` entries of a stream that would otherwise never end."""
    read = 0
    async for entry in entries:
        yield entry
        read += 1
        if read == many:
            return

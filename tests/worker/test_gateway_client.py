"""The worker's one door: what it asks the gateway, and what it does when the answer is no."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from pinecall.session.lookup_tools import Lookup
from pinecall.session.remember_step import Rememberer
from pinecall.session.voice.platform import Platform
from pinecall.types import AgentConfig, CallContext, Route, ToolSpec
from pinecall.worker import retries
from pinecall.worker.gateway_client import (
    CONFIG,
    CONTEXT,
    ROUTES,
    Gateway,
    build_gateway,
)
from pinecall.worker.gateway_http import EVENT_STREAM, GatewayRefused
from tests.worker.fakes import Seen, a_gateway

pytestmark = pytest.mark.unit

CLINICA = Route(org="pinecall", agent="clinica-norte", channel="phone", number="+59891111")
CLARA = AgentConfig(
    slug="clinica-norte",
    tools=(ToolSpec(name="find_slot", description="Free slots", parameters={"type": "object"}),),
)


async def test_the_routes_come_back_as_the_domain_holds_them() -> None:
    seen: list[Seen] = []
    gateway = a_gateway({"/v1/routes": ROUTES.dump_python((CLINICA,), mode="json")}, seen)
    assert await gateway.routes() == (CLINICA,)
    assert [(one.method, one.path) for one in seen] == [("GET", "/v1/routes")]


async def test_the_three_whose_doors_are_asked_with_the_corner_the_dispatch_named() -> None:
    """The worker holds one key for every org: the query string says whose doors it wants."""
    asked: list[tuple[str, dict[str, str]]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append((request.url.path, dict(request.url.params)))
        return httpx.Response(200, json=[] if request.url.path == "/v1/routes" else {"keys": {}})

    gateway = Gateway(httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://g"))
    await gateway.routes(org="tienda", env="sandbox", holder="m_1")
    await gateway.routes(number="+34910000099", channel="phone")
    await gateway.routes()
    await gateway.provider_keys("tienda-sur", org="tienda", env="production")
    assert asked == [
        ("/v1/routes", {"org": "tienda", "env": "sandbox", "holder": "m_1"}),
        ("/v1/routes", {"number": "+34910000099", "channel": "phone"}),
        ("/v1/routes", {}),
        ("/v1/agents/tienda-sur/provider-keys", {"org": "tienda", "env": "production"}),
    ]


async def test_an_agents_declaration_survives_the_hop_whole() -> None:
    """The two processes exchange the class they both hold: no second wire-to-domain conversion."""
    gateway = a_gateway({"/v1/agents/clinica-norte/config": CONFIG.dump_python(CLARA, mode="json")})
    assert await gateway.agent("clinica-norte") == CLARA


async def test_opening_a_call_sends_the_context_and_the_agent_it_is_for() -> None:
    seen: list[Seen] = []
    await a_gateway(seen=seen).opened(_a_call(), "clinica-norte")
    assert seen[0].path == "/v1/calls"
    assert seen[0].body["agent"] == "clinica-norte"
    assert CONTEXT.validate_python(seen[0].body["context"]) == _a_call()


async def test_a_call_names_the_app_socket_the_worker_was_started_with() -> None:
    """`pinecall talk` is served by the terminal it was typed in, and this is how it asks."""
    seen: list[Seen] = []
    await a_gateway(seen=seen).opened(_a_call(), "clinica-norte", "app_7c1e")
    assert seen[0].body["app"] == "app_7c1e"


async def test_a_call_that_names_no_app_socket_leaves_the_field_out() -> None:
    """A org worker on a box claims nobody: the gateway gives it the newest holder."""
    seen: list[Seen] = []
    await a_gateway(seen=seen).opened(_a_call(), "clinica-norte")
    assert "app" not in seen[0].body


async def test_an_entry_goes_to_the_call_it_belongs_to() -> None:
    seen: list[Seen] = []
    await a_gateway(seen=seen).append("call_1", "agent.state", {"state": "thinking"}, True)
    assert seen[0].path == "/v1/calls/call_1/events"
    assert seen[0].body == {"type": "agent.state", "data": {"state": "thinking"}, "ephemeral": True}


async def test_a_lookup_goes_out_with_its_tool_and_its_input_and_comes_back_as_an_object() -> None:
    seen: list[Seen] = []
    answered = {"output": {"chunks": [{"path": "tarifas.md", "text": "45 €."}]}, "took_ms": 41.0}
    gateway = a_gateway({"/v1/calls/call_1/lookup": answered}, seen)
    found = await gateway.lookup("call_1", "search", {"query": "¿cuánto cuesta?"}, "sp_3")
    assert found == {"chunks": [{"path": "tarifas.md", "text": "45 €."}]}
    assert (seen[0].method, seen[0].path) == ("POST", "/v1/calls/call_1/lookup")
    assert seen[0].body == {
        "tool": "search",
        "input": {"query": "¿cuánto cuesta?"},
        "speech_id": "sp_3",
    }


async def test_a_lookup_outside_a_speech_sends_no_speech_id() -> None:
    seen: list[Seen] = []
    gateway = a_gateway(
        {"/v1/calls/call_1/lookup": {"output": {"facts": []}, "took_ms": 3.0}}, seen
    )
    assert await gateway.lookup("call_1", "recall", {"query": "hola"}, None) == {"facts": []}
    assert "speech_id" not in seen[0].body


async def test_remember_knocks_at_the_calls_own_door_with_an_empty_body() -> None:
    seen: list[Seen] = []
    answered = {"/v1/calls/call_1/remember": {"ops": 2, "took_ms": 900.0}}
    assert await a_gateway(answered, seen).remember("call_1") == 2
    assert (seen[0].method, seen[0].path, seen[0].body) == ("POST", "/v1/calls/call_1/remember", {})


async def test_a_keyed_code_is_claimed_at_the_calls_own_door() -> None:
    seen: list[Seen] = []
    assert await a_gateway(seen=seen).claim("call_1", "4821") is True
    assert (seen[0].method, seen[0].path, seen[0].body) == (
        "POST",
        "/v1/calls/call_1/claim",
        {"code": "4821"},
    )


async def test_a_code_nobody_issued_is_a_no_and_any_other_refusal_is_raised() -> None:
    def answering(status: int) -> Gateway:
        transport = httpx.MockTransport(lambda _: httpx.Response(status, json={"detail": "no"}))
        return Gateway(httpx.AsyncClient(transport=transport, base_url="http://gateway.test"))

    assert await answering(404).claim("call_1", "4821") is False
    with pytest.raises(GatewayRefused, match="403"):
        await answering(403).claim("call_1", "4821")


def test_the_gateway_is_the_voice_sessions_platform_lookup_and_rememberer_in_one_object() -> None:
    """Three protocols, one door: what the session asks of the platform, the gateway answers."""
    gateway = a_gateway()
    platform: Platform = gateway
    lookup: Lookup = gateway
    rememberer: Rememberer = gateway
    assert platform is lookup is rememberer


async def test_the_end_of_a_call_seals_its_log() -> None:
    seen: list[Seen] = []
    await a_gateway(seen=seen).sealed("call_1")
    assert (seen[0].method, seen[0].path) == ("POST", "/v1/calls/call_1/sealed")


async def test_a_refusal_names_the_request_that_was_refused() -> None:
    with pytest.raises(GatewayRefused, match="GET /v1/routes: 404"):
        await a_gateway().routes()


async def test_a_gateway_that_is_not_there_is_a_refusal_and_never_a_traceback() -> None:
    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    gateway = Gateway(httpx.AsyncClient(transport=httpx.MockTransport(unreachable)))
    with pytest.raises(GatewayRefused, match="connection refused"):
        await gateway.agent("clinica-norte")


def test_the_key_travels_as_a_bearer_header_and_never_in_the_url() -> None:
    reached = build_gateway("http://gateway.internal", key="ops-key")
    assert reached._http.headers["authorization"] == "Bearer ops-key"  # pyright: ignore[reportPrivateUsage]


def _a_call() -> CallContext:
    """One call as the worker resolved it, before anybody has spoken."""
    return CallContext(
        call="call_1",
        channel="phone",
        direction="inbound",
        caller="+59897777",
        route=CLINICA,
        today=date(2026, 9, 6),
    )


async def test_the_state_comes_back_with_the_seq_it_was_folded_to() -> None:
    folded = {"state": {"seq": 7, "status": "live"}, "last_seq": 7, "live": True}
    gateway = a_gateway({"/v1/calls/call_1/state": folded})
    assert await gateway.state("call_1") == ({"seq": 7, "status": "live"}, 7)


async def test_since_pages_through_the_log_by_the_cursor_the_gateway_hands_back() -> None:
    seen: list[Seen] = []
    pages: dict[str, dict[str, Any]] = {
        "0": {"entries": [{"seq": 1}, {"seq": 2}], "live": True, "next": 2},
        "2": {"entries": [{"seq": 3}], "live": True, "next": 3},
        "3": {"entries": [], "live": True, "next": None},
    }

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(Seen(request.method, request.url.path, None))
        return httpx.Response(200, json=pages[request.url.params["after"]])

    gateway = Gateway(
        httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://gateway.test")
    )
    read = [entry async for entry in gateway.since("call_1", 0)]
    assert [entry["seq"] for entry in read] == [1, 2, 3]
    assert len(seen) == 3
    assert all(one.path == "/v1/calls/call_1/events" for one in seen)


async def test_since_stops_where_the_gateway_says_the_call_is_over() -> None:
    def answer(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 — the transport's shape
        return httpx.Response(204)

    gateway = Gateway(
        httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://gateway.test")
    )
    assert [entry async for entry in gateway.since("call_1", 9)] == []


async def test_tail_asks_for_the_stream_and_reads_each_frames_data() -> None:
    seen: list[str | None] = []
    body = (
        "retry: 1000\n\n"
        'id: 4\nevent: turn.user\ndata: {"seq": 4, "type": "turn.user"}\n\n'
        ": ping\n\n"
        'id: 5\nevent: call.ended\ndata: {"seq": 5,\ndata:  "type": "call.ended"}\n\n'
    )

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("accept"))
        return httpx.Response(200, text=body, headers={"content-type": EVENT_STREAM})

    gateway = Gateway(
        httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://gateway.test")
    )
    read = [entry async for entry in gateway.tail("call_1", 3)]
    assert read == [{"seq": 4, "type": "turn.user"}, {"seq": 5, "type": "call.ended"}]
    assert seen == [EVENT_STREAM]


async def test_a_refused_tail_is_a_refusal_naming_the_call() -> None:
    def answer(request: httpx.Request) -> httpx.Response:  # noqa: ARG001 — the transport's shape
        return httpx.Response(403, text="not yours")

    gateway = Gateway(
        httpx.AsyncClient(transport=httpx.MockTransport(answer), base_url="http://gateway.test")
    )
    with pytest.raises(GatewayRefused, match="GET /v1/calls/call_1/events"):
        _ = [entry async for entry in gateway.tail("call_1", 0)]


async def test_an_append_is_asked_again_while_the_gateway_is_away_and_lands_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def slept(_seconds: float) -> None:
        return None

    monkeypatch.setattr(retries.asyncio, "sleep", slept)
    answers = iter([httpx.Response(503), httpx.Response(502), httpx.Response(204)])
    asked: list[str] = []

    def answering(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return next(answers)

    transport = httpx.MockTransport(answering)
    gateway = Gateway(httpx.AsyncClient(transport=transport, base_url="http://gw.test"))
    await gateway.append("call_1", "turn.user", {"text": "hola"})
    assert asked == ["/v1/calls/call_1/events"] * 3

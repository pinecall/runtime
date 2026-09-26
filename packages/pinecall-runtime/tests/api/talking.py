"""How a test talks to the gateway: the frames, the sockets, the reads, and one call in and out."""

import asyncio
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from urllib.parse import quote

import httpx
from starlette.testclient import TestClient, WebSocketTestSession

from pinecall._settings import Settings
from pinecall.api import deps as deps
from pinecall.api.agents.held_agent import Send
from pinecall.api.app import app
from pinecall.auth.env import ENV_HEADER
from pinecall.log.entry import Entry
from pinecall.types import PRODUCTION, SANDBOX, CallContext, Env, Route
from pinecall.types.dispatch import DEFAULT_FLEET
from tests.api.conftest import A_KEY, A_RECORD, AGENT, APPS, CHAT, Json, over_the_asgi_app
from tests.conftest import a_sandbox


# An instance is one world. A test that follows a thing across both asks the same tables as the
# other world's instance from some line on: the rows are still told apart by their `env`, as they
# are inside each instance's own database.
def at_the_console(key: str, world: Env = PRODUCTION) -> httpx.AsyncClient:
    """A person's key as the console sends it: saying the world it believes the gateway is."""
    http = over_the_asgi_app(f"Bearer {key}")
    http.headers[ENV_HEADER] = world
    return http


def answering_in(world: Env, settings: Settings) -> Settings:
    """The test's gateway, from here on, is that world's instance over the same tables."""
    instance = (
        a_sandbox(settings)
        if world == SANDBOX
        else settings.model_copy(update={"world": PRODUCTION, "fleet": DEFAULT_FLEET})
    )
    app.dependency_overrides[deps.get_settings] = lambda: instance
    return instance


# starlette's TestClient is an httpx client, and httpx 0.x ships no stubs for the members a test
# uses; every GET in this package goes through this one deliberately untyped handle.
def got(
    client: TestClient, path: str, bearer: str | None = A_KEY, world: str | None = None
) -> tuple[int, Json]:
    """One GET at this door, as a status and, when the body is JSON, the body. `world` is the
    one a person's request names (auth/env.py); a server's token has its own."""
    headers = {} if bearer is None else {"Authorization": f"Bearer {bearer}"}
    if world is not None:
        headers[ENV_HEADER] = world
    handle: Any = client
    answer: Any = handle.get(path, headers=headers)
    status: int = answer.status_code
    body: Json = (
        answer.json() if answer.headers["content-type"].startswith("application/json") else {}
    )
    return status, body


def a_frame(
    type: str,
    agent: str,
    data: dict[str, object] | None = None,
    id: str | None = None,
    call: str | None = None,
) -> dict[str, object]:
    """One command as the wire carries it: type, agent, call, an optional id, and its data."""
    frame: dict[str, object] = {"type": type, "agent": agent, "call": call, "data": data or {}}
    if id is not None:
        frame["id"] = id
    return frame


def a_register(
    agent: str,
    *routes: dict[str, object],
    sdk: str | None = None,
    takes_unclaimed: bool | None = None,
) -> dict[str, object]:
    """An agent.register frame for an agent and the doors it claims."""
    data: dict[str, object] = {"routes": list(routes)}
    if sdk is not None:
        data["sdk"] = sdk
    if takes_unclaimed is not None:
        data["takes_unclaimed"] = takes_unclaimed
    return a_frame("agent.register", agent, data)


def an_app(gateway: TestClient) -> WebSocketTestSession:
    """The tenant's process on the app socket, with the key its org was issued."""
    return gateway.websocket_connect(APPS, headers={"Authorization": f"Bearer {A_KEY}"})


def a_caller(
    gateway: TestClient, agent: str = AGENT, contact: str | None = None
) -> WebSocketTestSession:
    """The caller on the chat socket, knocking with the org's key as `pinecall chat` does."""
    said = f"{CHAT}?agent={agent}"
    if contact is not None:
        said = f"{said}&contact={quote(contact, safe='')}"
    return gateway.websocket_connect(said, headers={"Authorization": f"Bearer {A_KEY}"})


def declared(
    app_socket: WebSocketTestSession,
    tools: Sequence[Mapping[str, object]] = (),
    events: Sequence[Mapping[str, object]] = (),
) -> None:
    """One agent, one web door, and whatever this test wants it to have declared."""
    app_socket.send_json(a_register(AGENT, a_door("web")))
    app_socket.receive_json()
    config: dict[str, object] = {"language": "es"}
    if tools:
        config["tools"] = [dict(tool) for tool in tools]
    if events:
        config["events"] = [dict(event) for event in events]
    app_socket.send_json(a_frame("agent.configure", AGENT, {"config": config}))
    app_socket.receive_json()


# A call as a worker would open it: the door it came through and who is on the line. The tests
# that serve a call by hand need one, because a served call carries its context for the fill.
def a_context(
    call: str, org: str = A_RECORD.org, channel: str = "web", caller: str = "visitor_1"
) -> CallContext:
    """One inbound call on the clinic's door, arriving on the day the suite is pinned to."""
    number = None if channel == "web" else "+34910000000"
    return CallContext(
        call=call,
        channel=channel,  # pyright: ignore[reportArgumentType] — a test names the channel as a word
        direction="inbound",
        caller=caller,
        route=Route(org=org, agent=AGENT, channel=channel, number=number),  # pyright: ignore[reportArgumentType]
        today=date(2026, 9, 7),
    )


def a_door(channel: str, number: str | None = None) -> dict[str, object]:
    """One route as the wire says it: a channel, and a number for the channels that have one."""
    return {"channel": channel, "number": number}


# A TestClient websocket is cancelled the moment its block exits, mid-anything the server is still
# doing — which is how a call cut off inside AgentSession.aclose() left livekit's on_exit coroutine
# unawaited and failed whichever test the warning surfaced in (filterwarnings=error). So a test
# that opens a caller ends the call from the app side and reads it to its summary first.
def entry_until(
    app_socket: WebSocketTestSession,
    type: str,
    keeping: list[dict[str, Any]] | None = None,
    most: int = 60,
) -> dict[str, Any]:
    """Read the app's socket, keeping every entry, until the one the test is waiting for."""
    for _ in range(most):
        entry: dict[str, Any] = app_socket.receive_json()
        if keeping is not None:
            keeping.append(entry)
        if entry["type"] == type:
            return entry
    raise AssertionError(f"the app never heard {type}")


def hung_up_by_the_app(app_socket: WebSocketTestSession, call: str) -> None:
    """The app ends the call and waits for its summary: the session closes before anyone leaves."""
    app_socket.send_json(a_frame("call.hangup", AGENT, {}, call=call))
    entry_until(app_socket, "call.summary")


# For the tests whose subject is what the DOOR built the call with — which model, whose keys —
# rather than anything said in it. The app ends the call for the reason written above.
def a_call_the_app_ends(gateway: TestClient, app_socket: WebSocketTestSession) -> str:
    """One caller in and out again, with the whole call written before anybody hangs up."""
    with a_caller(gateway):
        started = entry_until(app_socket, "call.started")
        call: str = started["call"]
        hung_up_by_the_app(app_socket, call)
        return call


def collecting(heard: list[Entry]) -> Send:
    """An app socket as the live memory holds one: every entry of its calls, as they are written."""

    async def send(entry: Entry) -> None:
        heard.append(entry)

    return send


async def until(heard: list[Entry], type: str) -> None:
    """Wait for one entry type to reach the app, so the test answers the tool it has asked for."""
    for _ in range(200):
        if any(entry.type == type for entry in heard):
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"{type} never reached the app socket")

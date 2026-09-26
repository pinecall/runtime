"""POST /v1/tokens against the real app: LiveKit's shape, minted only for the org's own agent."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import pytest
from livekit.api import TokenVerifier
from starlette.testclient import TestClient

from pinecall._settings import Settings
from pinecall.api import deps as deps
from pinecall.api.app import app
from pinecall.auth.scopes import SCOPE_ATTRIBUTE, THE_MICROPHONE
from pinecall.fleet import Heartbeat, Roster
from pinecall.log.store import MemoryStore
from pinecall.tokens.ledger import MemoryTokens
from pinecall.types.dispatch import (
    AGENT_KEY,
    CALLER_KEY,
    DEFAULT_FLEET,
    ENV_KEY,
    METADATA_KEY,
    ORG_KEY,
    SCOPE_KEY,
)
from tests.api.conftest import A_KEY, A_LIVEKIT, A_RECORD, AGENT, AN_OPS_KEY
from tests.api.talking import a_door, a_register, an_app

pytestmark = pytest.mark.unit

TOKENS = "/v1/tokens"
A_CONTACT = "c_9f3a2b"
# The five claims LiveKit's minter always writes, and the three ours adds. Anything else in a
# token would be a claim nobody asked for.
LIVEKITS_CLAIMS = {"sub", "iss", "nbf", "exp", "video"}
OUR_CLAIMS = {"metadata", "attributes", "roomConfig"}


def minted(
    gateway: TestClient, body: dict[str, Any] | None = None, bearer: str = A_KEY
) -> tuple[int, dict[str, Any]]:
    """One knock at the door, as a tenant's backend knocks: the org key, and LiveKit's body."""
    handle: Any = gateway
    answer: Any = handle.post(
        TOKENS, json=body if body is not None else {"agent": AGENT}, headers=_bearer(bearer)
    )
    status: int = answer.status_code
    said: dict[str, Any] = answer.json()
    return status, said


def payload_of(token: str) -> dict[str, Any]:
    """The JWT's claims as bytes on the wire, read without the library: what a grep would see."""
    _, payload, _ = token.split(".")
    padded = payload + "=" * (-len(payload) % 4)
    decoded: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded))
    return decoded


def the_dispatch_of(token: str, fleet: str = DEFAULT_FLEET) -> dict[str, Any]:
    """What the worker's router will read: the one dispatch's metadata, decoded."""
    claims = TokenVerifier(A_LIVEKIT.api_key, A_LIVEKIT.api_secret).verify(token)
    assert claims.room_config is not None
    agents = list(claims.room_config.agents)
    assert [one.agent_name for one in agents] == [fleet]
    said: dict[str, Any] = json.loads(agents[0].metadata)
    return said


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def test_the_answer_is_livekits_shape_and_livekits_own_verifier_reads_the_call(
    gateway: TestClient, tokens: MemoryTokens
) -> None:
    """201 with {server_url, participant_token}: what every client SDK's TokenSource expects."""
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        status, said = minted(
            gateway,
            {
                "agent": AGENT,
                "participant_identity": "web_the_visitor",
                "contact": A_CONTACT,
                "metadata": {"order": "o_77"},
            },
        )
    assert status == 201, said
    assert set(said) == {"server_url", "participant_token", "call", "log_token"}
    assert said["server_url"] == Settings(world="production").livekit_url
    claims = TokenVerifier(A_LIVEKIT.api_key, A_LIVEKIT.api_secret).verify(
        said["participant_token"]
    )
    assert claims.video is not None and claims.video.room == said["call"]
    assert claims.video.room_join and claims.video.can_publish and claims.video.can_subscribe
    assert claims.video.can_publish_sources == [THE_MICROPHONE]
    assert claims.identity == "web_the_visitor"
    assert claims.metadata == A_CONTACT
    assert (claims.attributes or {})[SCOPE_ATTRIBUTE] == "talk"
    # Whose call it is rides the dispatch too — the minting key's org and world — so the one
    # worker every org shares asks for THIS org's doors. Production names no holder.
    assert the_dispatch_of(said["participant_token"]) == {
        AGENT_KEY: AGENT,
        SCOPE_KEY: "talk",
        CALLER_KEY: "web_the_visitor",
        METADATA_KEY: {"order": "o_77"},
        ORG_KEY: A_RECORD.org,
        ENV_KEY: "production",
    }
    # The ledger holds it, unspent: the dispatch will spend it once.
    assert await tokens.spend(str(said["call"])) == "spent"


def test_participant_metadata_carries_the_contact_id_and_nothing_pii(gateway: TestClient) -> None:
    """Criterion 3, by grepping the minted JWT: the contact is in it, and no name or number is."""
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        refused, why = minted(gateway, {"agent": AGENT, "participant_name": "Juan Pérez"})
        status, said = minted(gateway, {"agent": AGENT, "contact": A_CONTACT})
    assert refused == 400 and "participant_name" in why["detail"] and "PII" in why["detail"]
    assert status == 201
    payload = payload_of(said["participant_token"])
    assert payload["metadata"] == A_CONTACT
    assert set(payload) == LIVEKITS_CLAIMS | OUR_CLAIMS
    on_the_wire = json.dumps(payload)
    assert "Juan" not in on_the_wire and "+34" not in on_the_wire


def test_a_stock_client_names_the_agent_as_agentname_and_either_spelling_is_read(
    gateway: TestClient,
) -> None:
    """livekit-client packages `agentName` into room_config; a body with no `agent` still mints."""
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        snake, _ = minted(gateway, {"room_config": {"agents": [{"agent_name": AGENT}]}})
        camel, said = minted(gateway, {"room_config": {"agents": [{"agentName": AGENT}]}})
        nobody, why = minted(gateway, {})
    assert (snake, camel) == (201, 201)
    assert the_dispatch_of(said["participant_token"])[AGENT_KEY] == AGENT
    assert nobody == 400 and "agentName" in why["detail"]


def test_a_malformed_room_config_is_refused_in_the_parsers_words(gateway: TestClient) -> None:
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        status, why = minted(gateway, {"room_config": {"agents": "not a list"}})
    assert status == 400 and "RoomConfiguration" in why["detail"]


# There is no web door to hold: a number is a row somebody bought and a browser is not, so an
# agent with a telephone and no widget is an agent you can still talk to from a page. What is
# still refused is an agent NOBODY is holding — a token for one is a browser joining a room that
# nothing will ever answer in.
def test_an_agent_with_a_number_and_no_widget_is_talked_to_all_the_same(
    gateway: TestClient,
) -> None:
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("phone", "+34910000000")))
        app_socket.receive_json()
        phone_only, _ = minted(gateway, {"agent": AGENT})
    assert phone_only == 201


def test_an_agent_nobody_is_holding_is_refused(gateway: TestClient) -> None:
    """The config door's own 404, in its own words: no app is holding that agent."""
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        unknown, why = minted(gateway, {"agent": "nobody"})
    assert unknown == 404
    assert "nobody" in why["detail"]


def test_the_body_may_not_set_what_is_minted_here(gateway: TestClient) -> None:
    """The LiveKit page's rule: a 4xx for the fields a client is not allowed to set."""
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        refusals = {
            field: minted(gateway, {"agent": AGENT, field: value})
            for field, value in {
                "room_name": "my-room",
                "participant_metadata": "anything",
                "participant_attributes": {"pinecall.scope": "supervise"},
                "scope": "supervise",
            }.items()
        }
    for field, (status, why) in refusals.items():
        assert status == 400, field
        assert field.split("_")[0] in why["detail"] or "pinecall." in why["detail"], field


def test_a_chat_token_has_no_microphone_and_hears_the_room(gateway: TestClient) -> None:
    """Hearing is how the agent's text streams reach the page; the page attaches no audio."""
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        status, said = minted(gateway, {"agent": AGENT, "scope": "chat"})
    assert status == 201
    claims = TokenVerifier(A_LIVEKIT.api_key, A_LIVEKIT.api_secret).verify(
        said["participant_token"]
    )
    assert claims.video is not None
    assert (claims.video.can_publish, claims.video.can_subscribe) == (False, True)
    assert claims.video.can_publish_data
    assert the_dispatch_of(said["participant_token"])[SCOPE_KEY] == "chat"


def test_the_ttl_is_a_minute_by_default_and_ten_at_most(gateway: TestClient) -> None:
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        _, a_minute = minted(gateway, {"agent": AGENT})
        _, ten_minutes = minted(gateway, {"agent": AGENT, "ttl_s": 600})
        too_long, _ = minted(gateway, {"agent": AGENT, "ttl_s": 601})
    first = payload_of(a_minute["participant_token"])
    second = payload_of(ten_minutes["participant_token"])
    assert first["exp"] - first["nbf"] == 60
    assert second["exp"] - second["nbf"] == 600
    assert too_long == 422


def test_the_browser_is_told_the_public_url_when_the_box_has_one(gateway: TestClient) -> None:
    """A box reaches LiveKit on localhost; a browser cannot, so server_url is the public one."""
    public = Settings(
        world="production",
        ops_key=AN_OPS_KEY,
        livekit_api_key=A_LIVEKIT.api_key,
        livekit_api_secret=A_LIVEKIT.api_secret,
        livekit_public_url="wss://livekit.clinica.example",
    )
    app.dependency_overrides[deps.get_settings] = lambda: public
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        status, said = minted(gateway, {"agent": AGENT})
    assert status == 201 and said["server_url"] == "wss://livekit.clinica.example"


def test_the_dispatch_asks_for_the_fleet_this_instance_names(
    gateway: TestClient, settings: Settings
) -> None:
    """Two instances share one SFU: the fleet in the token is what keeps the call on this one."""
    ours = settings.model_copy(update={"fleet": "pinecall-sandbox"})
    app.dependency_overrides[deps.get_settings] = lambda: ours
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        status, said = minted(gateway, {"agent": AGENT})
    assert status == 201
    assert the_dispatch_of(said["participant_token"], fleet="pinecall-sandbox")[AGENT_KEY] == AGENT


def test_the_door_takes_the_api_key_and_nothing_else(gateway: TestClient) -> None:
    """The key never reaches a browser: only the tenant's backend can knock here."""
    nobody, _ = minted(gateway, bearer="pk_not_a_key_anybody_issued")
    assert nobody == 401


# Every worker full: a token minted now would open a room nobody joins, so the door says no with
# the numbers and the callback door, and writes fleet.full into the agent's log first — the same
# shape as credits.exhausted. A roster nobody knocked at (every other test here) refuses nothing.
def test_a_full_fleet_refuses_the_token_with_a_503_and_writes_fleet_full(
    gateway: TestClient, store: MemoryStore, fleet: Roster
) -> None:
    fleet.report(
        Heartbeat(worker="w1", active=3, max_jobs=4, load=0.75, draining=False), time.time()
    )
    with an_app(gateway) as app_socket:
        app_socket.send_json(a_register(AGENT, a_door("web")))
        app_socket.receive_json()
        status, said = minted(gateway)
    assert status == 503
    assert "3 calls on 1 workers" in said["detail"] and "/v1/callbacks" in said["detail"]
    written = asyncio.run(store.agent_since(AGENT))
    # The socket closed after the refusal, so the log ends on agent.detached; fleet.full is the
    # entry before it, written before the 503 went out.
    assert [entry.type for entry in written[-2:]] == ["fleet.full", "agent.detached"]
    assert written[-2].data == {"channel": "web", "workers": 1, "active": 3}

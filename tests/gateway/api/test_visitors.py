"""Tests for a visitor's doors: room tokens and the codes a caller keys."""

import asyncio
import json
import time
from dataclasses import replace

import jwt
import pytest

from pinecall.domain.names import Env
from pinecall.gateway.app import app as app_of_gateway
from pinecall.process.settings import Settings
from pinecall.tenancy import tokens
from pinecall.wire.rest.calls import OpenCallRequest
from pinecall.wire.rest.fleet import HeartbeatRequest
from tests.conftest import (
    AGENT,
    Knocking,
    postgres,
)
from tests.gateway.api.conftest import A_NUMBER, a_call, an_app, bound_to


async def a_route(knocking: Knocking, env: Env = "sandbox") -> None:
    """The agent answers at the number in the world."""
    async with knocking.gateway.connections.pool.connection() as connection:
        await connection.execute(
            "insert into routes (org, number, agent, channel, env) "
            "values (%s, %s, %s, 'phone', %s)",
            (knocking.org.id, A_NUMBER, AGENT, env),
        )


@postgres
async def test_a_visitors_token_carries_the_dispatch_to_its_worlds_fleet(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        minted = await tenant.post("/v1/tokens", json={"agent": AGENT, "contact": "c_42"})
    assert minted.status_code == 201
    answer = minted.json()
    claims = jwt.decode(answer["participant_token"], options={"verify_signature": False})
    dispatch = claims["roomConfig"]["agents"][0]
    assert dispatch["agentName"] == "pinecall-sandbox"
    carried = json.loads(dispatch["metadata"])
    assert carried["org"] == knocking.org.id
    assert carried["env"] == "sandbox"
    assert carried["contact"] == "c_42"
    assert claims["video"]["room"] == answer["call"]
    await app.close()


@postgres
@pytest.mark.parametrize("field", ["room_name", "participant_name", "participant_metadata"])
async def test_what_the_door_mints_is_refused_in_the_body(knocking: Knocking, field: str) -> None:
    app = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        refused = await tenant.post("/v1/tokens", json={"agent": AGENT, field: "x"})
    assert refused.status_code == 400
    assert field in refused.json()["detail"]
    await app.close()


@postgres
async def test_an_agent_nobody_holds_gets_no_token(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        refused = await tenant.post("/v1/tokens", json={"agent": AGENT})
    assert refused.status_code == 404


@postgres
async def test_a_full_fleet_refuses_the_token_and_the_agents_log_says_so(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    beat = HeartbeatRequest(
        fleet="pinecall-sandbox", worker="w1", active=4, max_jobs=4, load=1.0, draining=False
    )
    knocking.gateway.roster.report(beat, time.time())
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        refused = await tenant.post("/v1/tokens", json={"agent": AGENT})
    assert refused.status_code == 503
    assert "every seat" in refused.json()["detail"]
    written = await knocking.gateway.logs.store.whole(f"@{AGENT}")
    assert (written[-1].type, written[-1].data["workers"]) == ("fleet.full", 1)
    await app.close()


@postgres
async def test_a_full_sandbox_leaves_production_open(knocking: Knocking) -> None:
    app = await an_app(knocking, env="production")
    beat = HeartbeatRequest(
        fleet="pinecall-sandbox", worker="w1", active=4, max_jobs=4, load=1.0, draining=False
    )
    knocking.gateway.roster.report(beat, time.time())
    async with knocking.http(knocking.app["production"]) as tenant:
        minted = await tenant.post("/v1/tokens", json={"agent": AGENT})
    assert minted.status_code == 201
    await app.close()


@postgres
async def test_a_code_is_claimed_by_the_call_that_keys_it_and_the_page_is_told(
    knocking: Knocking,
) -> None:
    await a_route(knocking)
    app = await an_app(knocking)
    context = a_call(knocking)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        issued = (await tenant.post("/v1/codes", json={"agent": AGENT})).json()
    assert issued["number"] == A_NUMBER
    async with knocking.http(knocking.fleet["sandbox"]) as worker:
        await worker.post("/v1/calls", json=OpenCallRequest(agent=AGENT, context=context).written())
        claim = await worker.post(f"/v1/calls/{context.call}/claim", json={"code": issued["code"]})
        nobodys = await worker.post(f"/v1/calls/{context.call}/claim", json={"code": "0000"})
    assert claim.status_code == 204
    assert nobodys.status_code == 404
    async with knocking.http(issued["code_token"]) as page:
        existing = (await page.get(f"/v1/codes/{issued['code']}")).json()
    assert existing["status"] == "claimed"
    assert existing["call"] == context.call
    await app.close()


@postgres
async def test_a_code_needs_a_number_to_be_called_at(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        refused = await tenant.post("/v1/codes", json={"agent": AGENT})
    assert refused.status_code == 409


@postgres
async def test_the_ttl_is_a_minute_by_default_and_ten_at_most(knocking: Knocking) -> None:
    app = await an_app(knocking)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        short = (await tenant.post("/v1/tokens", json={"agent": AGENT})).json()
        long = (await tenant.post("/v1/tokens", json={"agent": AGENT, "ttl_s": 99_999})).json()
    lasts = [
        claims["exp"] - claims["nbf"]
        for claims in (
            jwt.decode(item["participant_token"], options={"verify_signature": False})
            for item in (short, long)
        )
    ]
    assert lasts[0] in (60, 61)
    assert lasts[1] in (600, 601)
    await app.close()


@postgres
async def test_a_chat_token_has_no_microphone_and_the_browser_is_told_the_public_url(
    knocking: Knocking,
) -> None:
    app = await an_app(knocking)
    public = Settings.model_validate(
        {
            **knocking.gateway.connections.settings.variables,
            "LIVEKIT_PUBLIC_URL": "wss://sfu.example",
        }
    )
    app_of_gateway.state.gateway = replace(
        knocking.gateway, connections=replace(knocking.gateway.connections, settings=public)
    )
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        minted = (await tenant.post("/v1/tokens", json={"agent": AGENT, "scope": "chat"})).json()
    claims = jwt.decode(minted["participant_token"], options={"verify_signature": False})
    assert claims["video"]["canPublish"] is False
    assert minted["server_url"] == "wss://sfu.example"
    await app.close()


@postgres
async def test_an_expired_code_says_so_and_a_code_token_reads_nothing_but_its_code(
    knocking: Knocking,
) -> None:
    await a_route(knocking)
    async with knocking.http(knocking.app["sandbox"]) as tenant:
        issued_now = (await tenant.post("/v1/codes", json={"agent": AGENT, "ttl_s": 1})).json()
    await asyncio.sleep(1.1)
    async with knocking.http(issued_now["code_token"]) as page:
        found = await page.get(f"/v1/codes/{issued_now['code']}")
        another = await page.get("/v1/codes/0000")
    assert found.status_code == 200, found.text
    assert found.json()["status"] == "expired"
    assert another.status_code == 403


def test_a_room_token_reads_as_its_call() -> None:
    signer = tokens.Signer("APIkey", "a secret of thirty-two bytes or more")
    visitor = tokens.Visitor(expires_at=time.time() + 60, identity="web_1")
    token = tokens.room_token(signer, "call_1", "talk", visitor)
    visit = tokens.read(signer, token)
    assert visit is not None
    assert visit.call == "call_1"


@postgres
async def test_a_member_bound_to_other_agents_mints_no_token_and_no_code_for_this_one(
    knocking: Knocking,
) -> None:
    socket = await an_app(knocking)
    elsewhere = await bound_to(knocking, "bo@clinica.test", frozenset({"ventas"}))
    async with knocking.http(elsewhere) as person:
        token = await person.post("/v1/tokens", json={"agent": AGENT})
        code = await person.post("/v1/codes", json={"agent": AGENT})
    assert (token.status_code, code.status_code) == (403, 403)
    assert f"agent {AGENT} is not one of them" in token.json()["detail"]
    await socket.close()

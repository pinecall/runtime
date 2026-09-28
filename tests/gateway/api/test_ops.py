"""Tests for the box's own doors: whose operator key this is."""

from dataclasses import replace

from pinecall.gateway.app import app
from pinecall.tenancy import people
from tests.conftest import Knocking, a_developer, postgres

THE_OPS_KEY = "the-operators-own-key-of-this-box"


def with_an_ops_key(knocking: Knocking) -> None:
    """The same gateway, its operator key set."""
    settings = knocking.gateway.connections.settings.model_copy(update={"ops_key": THE_OPS_KEY})
    connections = replace(knocking.gateway.connections, settings=settings)
    app.state.gateway = replace(knocking.gateway, connections=connections)


@postgres
async def test_the_boxs_own_key_opens_it_and_names_nobody(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    async with knocking.http(THE_OPS_KEY) as operator:
        answer = await operator.get("/v1/ops/whoami")
    body = answer.json()
    assert answer.status_code == 200
    assert (body["operator"], body["domain"], body["name"], body["org"]) == (
        True,
        "box.test",
        None,
        None,
    )
    assert body["version"]
    assert THE_OPS_KEY not in answer.text


@postgres
async def test_a_person_the_box_made_an_operator_is_named_with_their_org(
    knocking: Knocking,
) -> None:
    member, secret = await a_developer(knocking, "ana@clinica.test")
    await people.make_operator(knocking.gateway.connections.pool, knocking.org.id, member, on=True)
    async with knocking.http(secret) as operator:
        body = (await operator.get("/v1/ops/whoami")).json()
    assert (body["name"], body["org"]) == ("ana", knocking.org.id)


@postgres
async def test_the_ops_doors_take_the_ops_key_and_nothing_else(knocking: Knocking) -> None:
    with_an_ops_key(knocking)
    _, developer = await a_developer(knocking, "bo@clinica.test")
    async with knocking.http(knocking.app["production"]) as server:
        by_a_server = await server.get("/v1/ops/whoami")
    async with knocking.http(developer) as person:
        by_a_person = await person.get("/v1/ops/whoami")
    async with knocking.http("the-wrong-ops-key") as wrong:
        by_a_guess = await wrong.get("/v1/ops/whoami")
    assert [by_a_server.status_code, by_a_person.status_code, by_a_guess.status_code] == [401] * 3

"""Tests for the developer's line: whose terminal a ring lands in."""

from tests.conftest import (
    AGENT,
    BOX_DOMAIN,
    Knocking,
    a_developer,
    postgres,
)
from tests.gateway.api.conftest import HER_PHONE, an_app


@postgres
async def test_the_first_terminal_to_hold_the_agent_answers_its_ring(knocking: Knocking) -> None:
    ana, ana_key = await a_developer(knocking, "ana@clinica.test")
    _, ben_key = await a_developer(knocking, "ben@clinica.test")
    first = await an_app(knocking, ana_key)
    later = await an_app(knocking, ben_key)
    async with knocking.http(ben_key) as ben:
        line = (await ben.get(f"/v1/agents/{AGENT}/line")).json()
    assert line["held"] is True
    assert line["holding"]["holder"] == ana
    assert line["yours"] is False
    await first.close()
    await later.close()


@postgres
async def test_a_claim_takes_the_line_and_dropping_it_hands_it_back(knocking: Knocking) -> None:
    ana, ana_key = await a_developer(knocking, "ana@clinica.test")
    ben, ben_key = await a_developer(knocking, "ben@clinica.test")
    first = await an_app(knocking, ana_key)
    later = await an_app(knocking, ben_key)
    async with knocking.http(ben_key) as asking:
        taken = (await asking.post(f"/v1/agents/{AGENT}/line")).json()
        dropped = (await asking.delete(f"/v1/agents/{AGENT}/line")).json()
    assert (taken["holding"]["holder"], taken["yours"]) == (ben, True)
    assert dropped["holding"]["holder"] == ana
    await first.close()
    await later.close()


@postgres
async def test_a_developers_phone_on_the_production_number_reaches_their_copy(
    knocking: Knocking,
) -> None:
    ana, ana_key = await a_developer(knocking, "ana@clinica.test")
    copy = await an_app(knocking, ana_key)
    async with knocking.http(ana_key) as her:
        said_back = (await her.put("/v1/line/from", json={"number": HER_PHONE})).json()
    params = {"org": knocking.org.id, "caller": HER_PHONE}
    stranger = {"org": knocking.org.id, "caller": "+59899999999"}
    async with knocking.http(knocking.fleet["production"]) as worker:
        handed = (await worker.get(f"/v1/agents/{AGENT}/rings-for", params=params)).json()
        kept = (await worker.get(f"/v1/agents/{AGENT}/rings-for", params=stranger)).json()
    assert said_back == {"calling": [HER_PHONE]}
    assert (handed["holder"], handed["fleet"]) == (ana, "pinecall-sandbox")
    # The test's box gives each world a LiveKit of its own: the ring is dialled to the sandbox's
    # SIP, shown as the caller.
    assert (handed["trunk"]["hostname"], handed["trunk"]["shown"]) == (BOX_DOMAIN, HER_PHONE)
    assert kept == {"holder": None, "fleet": None, "trunk": None}
    await copy.close()


@postgres
async def test_a_server_key_has_no_person_to_route_a_phone_to(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["sandbox"]) as server:
        refused = await server.put("/v1/line/from", json={"number": HER_PHONE})
    assert refused.status_code == 403

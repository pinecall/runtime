"""The other instance asked on a peer key: a hand-over or nobody's, routes, and silence refused."""

from dataclasses import asdict

import httpx
import pytest

from pinecall.auth.peers import ROUTES, TIMEOUT_S, Peer, PeerUnreachable, RingsFor
from pinecall.types import PRODUCTION, Route
from pinecall.types.dispatch import Handover

pytestmark = pytest.mark.unit

THERE = "https://sandbox.example.test"
A_PEER_KEY = "pk_test_minted_there_for_here"
A_DOOR = Route("org_1", "clinica", "phone", "+34910000000")


def answering(response: httpx.Response | Exception, asked: list[httpx.Request]) -> Peer:
    """The other instance at THERE, answering every request with this; the trailing slash is one."""

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        if isinstance(response, Exception):
            raise response
        return response

    return Peer(httpx.AsyncClient(transport=httpx.MockTransport(handle)), f"{THERE}/", A_PEER_KEY)


async def test_a_ring_that_is_a_developers_comes_back_as_their_corner_and_their_fleet() -> None:
    asked: list[httpx.Request] = []
    said = {"holder": "m_berna", "fleet": "pinecall-sandbox"}
    peer = answering(httpx.Response(200, json=said), asked)

    handover = await peer.rings_for("clinica", org="org_1", caller="+34600123456")

    assert handover == Handover(holder="m_berna", fleet="pinecall-sandbox")
    (request,) = asked
    assert str(request.url).startswith(f"{THERE}/v1/agents/clinica/rings-for?")
    assert request.headers["Authorization"] == f"Bearer {A_PEER_KEY}"
    assert request.extensions["timeout"]["read"] == TIMEOUT_S


async def test_nobodys_ring_is_none() -> None:
    peer = answering(httpx.Response(200, json=RingsFor().model_dump()), [])
    assert await peer.rings_for("clinica", org="org_1", caller="+34600123456") is None


@pytest.mark.parametrize(
    "answer",
    [
        httpx.ConnectError("nobody home"),
        httpx.ReadTimeout("two seconds went by"),
        httpx.Response(403, json={"detail": "this token was made for production"}),
        httpx.Response(200, text="<html>a proxy</html>"),
        httpx.Response(200, json={"holder": "m_berna", "fleet": "x", "more": 1}),
    ],
)
async def test_silence_a_refusal_or_what_is_not_its_door_is_the_peer_not_answering(
    answer: httpx.Response | Exception,
) -> None:
    with pytest.raises(PeerUnreachable, match=THERE):
        await answering(answer, []).rings_for("clinica", org="org_1", caller="+34600123456")


async def test_productions_routes_are_asked_for_one_org_in_production() -> None:
    asked: list[httpx.Request] = []
    peer = answering(httpx.Response(200, json=[asdict(A_DOOR)]), asked)

    assert await peer.production_routes_of("org_1") == (A_DOOR,)
    (request,) = asked
    assert (request.url.path, dict(request.url.params)) == (
        ROUTES,
        {"org": "org_1", "env": PRODUCTION},
    )


def test_an_answer_is_both_halves_or_nobodys() -> None:
    assert RingsFor(holder="m_berna").handover() is None
    assert RingsFor.of(None) == RingsFor()

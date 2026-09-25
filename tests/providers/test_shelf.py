"""A vendor's voices, asked of the vendor: paged, judged by language again, and refused by name."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from pinecall._settings import Settings
from pinecall.providers.registry import Asked, NoProvider
from pinecall.providers.tts.shelf import NotListed, Shelf, ShelfUnreachable

pytestmark = pytest.mark.unit

CARTESIA = "https://api.cartesia.test"
A_KEY = "sk_car_the_orgs_own"

MARTA = {
    "id": "de38f545-c574-44e8-9b54-a7d6fec1c6b1",
    "name": "Marta - Friendly Guide",
    "language": "es",
    "description": "Approachable Spanish female ideal for customer care and support.",
    "gender": "feminine",
    "country": "ES",
    "accents": [{"accent": "castilian", "locale": "es-ES", "is_native": True}],
}
MATEO = {"id": "2fc4f1ec", "name": "Mateo - Friendly Host", "language": "es", "country": "MX"}
SKYLAR = {"id": "db6b0ed5", "name": "Skylar - Friendly Guide", "language": "en", "country": "US"}


def a_shelf(answer: Callable[[httpx.Request], httpx.Response]) -> Shelf:
    return Shelf(httpx.AsyncClient(transport=httpx.MockTransport(answer)), cartesia=CARTESIA)


def asked() -> Asked:
    """The org brought its own Cartesia key: the box has none in this suite."""
    return Asked(settings=Settings(), keys={"cartesia": A_KEY})


async def test_cartesia_is_read_page_by_page_on_the_orgs_key() -> None:
    seen: list[httpx.Request] = []

    def served(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if "starting_after" not in request.url.params:
            return httpx.Response(200, json={"data": [MARTA], "has_more": True, "next_page": "x"})
        return httpx.Response(200, json={"data": [MATEO], "has_more": False, "next_page": None})

    voices = await a_shelf(served).voices("cartesia", "es", asked())

    assert [voice.name for voice in voices] == ["Marta - Friendly Guide", "Mateo - Friendly Host"]
    assert voices[0].country == "ES" and voices[0].accent == "castilian"
    assert voices[1].accent == "", "a row with no accents still reads"
    assert [request.url.params.get("starting_after") for request in seen] == [None, "x"]
    assert all(request.headers["X-API-Key"] == A_KEY for request in seen)
    assert seen[0].url.params["language"] == "es"


async def test_a_voice_of_another_language_the_vendor_let_through_is_dropped() -> None:
    def served(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [MARTA, SKYLAR], "has_more": False})

    voices = await a_shelf(served).voices("cartesia", "es", asked())

    assert [voice.id for voice in voices] == [MARTA["id"]]


async def test_elevenlabs_answers_with_the_names_this_build_curates() -> None:
    def never(_: httpx.Request) -> httpx.Response:
        raise AssertionError("elevenlabs is not asked")

    voices = await a_shelf(never).voices("11labs", None, asked())

    assert [voice.id for voice in voices] == ["carolina", "charlie", "mateo"]


async def test_a_vendor_whose_catalogue_is_not_read_is_refused_by_name() -> None:
    with pytest.raises(NotListed, match="rime"):
        await a_shelf(lambda _: httpx.Response(500)).voices("rime", "es", asked())


async def test_no_key_for_the_vendor_is_refused_before_it_is_asked() -> None:
    def never(_: httpx.Request) -> httpx.Response:
        raise AssertionError("no key, no request")

    bare = Asked(settings=Settings(cartesia_api_key=None))
    with pytest.raises(NoProvider, match="cartesia"):
        await a_shelf(never).voices("cartesia", "es", bare)


async def test_a_vendor_that_refuses_says_so_with_its_status() -> None:
    def refused(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(ShelfUnreachable, match="401"):
        await a_shelf(refused).voices("cartesia", "es", asked())

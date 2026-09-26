"""A vendor's voices, asked of the vendor: paged, judged by language again, and refused by name."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from pinecall.providers.registry import Asked, NoProvider
from pinecall.providers.tts.vendor_voices import PAGES_AT_MOST, NotListed, Shelf, ShelfUnreachable
from pinecall.settings import Settings

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
    return Asked(settings=Settings(world="production"), keys={"cartesia": A_KEY})


def one_page(*rows: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _: httpx.Response(200, json={"data": list(rows), "has_more": False})


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


# An agent declares `es-ES` or `en_US` as freely as `es`; the vendor files a voice under `es`. The
# picker was empty for every locale until the two were read through one normaliser.
async def test_a_locale_is_its_language_to_the_shelf() -> None:
    seen: list[httpx.Request] = []

    def served(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": [MARTA, SKYLAR], "has_more": False})

    voices = await a_shelf(served).voices("cartesia", "es-ES", asked())

    assert [voice.id for voice in voices] == [MARTA["id"]]
    assert seen[0].url.params["language"] == "es", "the vendor is asked in its own base code"
    assert [voice.id for voice in await a_shelf(served).voices("11labs", "en_US", asked())] == [
        "carolina",
        "charlie",
        "mateo",
    ]


async def test_a_voice_of_another_language_the_vendor_let_through_is_dropped() -> None:
    voices = await a_shelf(one_page(MARTA, SKYLAR)).voices("cartesia", "es", asked())

    assert [voice.id for voice in voices] == [MARTA["id"]]


async def test_a_row_with_no_id_is_passed_over_and_the_rest_still_read() -> None:
    voices = await a_shelf(one_page({"name": "nobody"}, MARTA)).voices("cartesia", None, asked())

    assert [voice.id for voice in voices] == [MARTA["id"]]


async def test_a_vendor_that_pages_for_ever_is_read_no_further_than_the_ceiling() -> None:
    pages = 0

    def endless(_: httpx.Request) -> httpx.Response:
        nonlocal pages
        pages += 1
        return httpx.Response(200, json={"data": [MARTA], "has_more": True, "next_page": "more"})

    voices = await a_shelf(endless).voices("cartesia", "es", asked())

    assert pages == PAGES_AT_MOST and len(voices) == PAGES_AT_MOST


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

    bare = Asked(settings=Settings(world="production", cartesia_api_key=None))
    with pytest.raises(NoProvider, match="cartesia"):
        await a_shelf(never).voices("cartesia", "es", bare)


async def test_a_vendor_that_refuses_says_so_and_keeps_its_status() -> None:
    def refused(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    with pytest.raises(ShelfUnreachable, match="401") as raised:
        await a_shelf(refused).voices("cartesia", "es", asked())
    assert raised.value.status == 401


async def test_a_vendor_that_does_not_answer_has_no_status_to_keep() -> None:
    def broke(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    with pytest.raises(ShelfUnreachable, match="no route") as raised:
        await a_shelf(broke).voices("cartesia", "es", asked())
    assert raised.value.status is None

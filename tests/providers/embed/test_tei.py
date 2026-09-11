"""TEI as the runtime speaks to it: one POST per batch, the model asked once, the width held."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from pinecall.providers.embed.tei import A_PUSH_MAY_TAKE_S, UNNAMED, TeiEmbedder
from pinecall.providers.embedder import DIMENSIONS, Embedder, EmbedderUnreachable, WrongWidth

pytestmark = pytest.mark.unit

TEI = "http://tei.test:8081"


def a_tei(
    model: str | None = "BAAI/bge-m3", width: int = DIMENSIONS
) -> tuple[TeiEmbedder, list[httpx.Request]]:
    """A TEI that answers /info and /embed from a transport that opens no socket."""
    seen: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/info":
            return httpx.Response(200, json={"model_id": model, "max_input_length": 8192})
        inputs = json.loads(request.content)["inputs"]
        return httpx.Response(200, json=[[0.5] * width for _ in inputs])

    embedder = TeiEmbedder(TEI, httpx.AsyncClient(transport=httpx.MockTransport(answer)))
    return embedder, seen


async def test_a_batch_is_one_post_with_the_inputs_and_truncation_on() -> None:
    embedder, seen = a_tei()
    vectors = await embedder.embed(["hola", "turno"])
    assert [len(vector) for vector in vectors] == [DIMENSIONS, DIMENSIONS]
    posted = _the_posts(seen)
    assert [request.url.path for request in posted] == ["/embed"]
    assert json.loads(posted[0].content) == {"inputs": ["hola", "turno"], "truncate": True}


async def test_the_model_is_the_one_tei_names_and_a_row_may_keep_that_name() -> None:
    embedder, seen = a_tei(model="BAAI/bge-m3")
    assert await embedder.model() == "BAAI/bge-m3"
    assert [request.url.path for request in seen] == ["/info"]


async def test_the_model_is_asked_once_however_many_batches_follow() -> None:
    embedder, seen = a_tei()
    await embedder.embed(["uno"])
    await embedder.embed(["dos"])
    assert [request.url.path for request in seen] == ["/info", "/embed", "/embed"]


async def test_a_model_of_another_width_is_refused_by_name_before_a_row_is_written() -> None:
    embedder, _seen = a_tei(model="sentence-transformers/all-MiniLM-L6-v2", width=384)
    with pytest.raises(WrongWidth, match="all-MiniLM-L6-v2 answers 384-wide vectors"):
        await embedder.embed(["hola"])


async def test_a_tei_that_names_no_model_is_still_refused_with_a_word_for_it() -> None:
    embedder, _seen = a_tei(model=None, width=768)
    with pytest.raises(WrongWidth, match=UNNAMED):
        await embedder.embed(["hola"])


async def test_nothing_to_embed_asks_nobody() -> None:
    embedder, seen = a_tei()
    assert await embedder.embed([]) == []
    assert seen == []


def test_tei_is_an_embedder_of_the_declared_width() -> None:
    embedder: Embedder = TeiEmbedder(TEI, httpx.AsyncClient())
    assert embedder.dimensions == DIMENSIONS


def _the_posts(seen: list[httpx.Request]) -> list[httpx.Request]:
    posted: Callable[[httpx.Request], Any] = lambda request: request.method == "POST"  # noqa: E731
    return [request for request in seen if posted(request)]


async def test_a_tei_that_refuses_the_connection_is_unreachable_by_name_and_url() -> None:
    """The sentence is what a fill's error entry carries, so it names the vendor and where."""

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    embedder = TeiEmbedder(TEI, httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    with pytest.raises(EmbedderUnreachable, match=f"TEI at {TEI} did not answer: connection"):
        await embedder.embed(["hola"])


async def test_a_tei_that_answers_5xx_is_unreachable_too() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    embedder = TeiEmbedder(TEI, httpx.AsyncClient(transport=httpx.MockTransport(broken)))
    with pytest.raises(EmbedderUnreachable, match="TEI at"):
        await embedder.model()


# The client's timeout is a lookup's, and a lookup is on a caller's clock. A push is on nobody's:
# a whole folder through a CPU embedder that may still be loading its model.
async def test_a_push_waits_for_a_cold_embedder_and_a_lookup_does_not() -> None:
    embedder, seen = a_tei()
    await embedder.embed(["una consulta"])
    await embedder.embed_documents([["un capítulo", "otro"]])
    posted = _the_posts(seen)
    lookup, push = (request.extensions["timeout"] for request in posted)
    assert lookup["read"] == httpx.AsyncClient().timeout.read
    assert push["read"] == A_PUSH_MAY_TAKE_S

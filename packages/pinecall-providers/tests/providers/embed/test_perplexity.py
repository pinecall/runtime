"""Perplexity as the runtime speaks to it: the flat door, the contextual door, and the refusals."""

from __future__ import annotations

import base64
import json
import math
from array import array
from collections.abc import Callable, Sequence
from typing import Any

import httpx
import pytest

from pinecall.providers.embed.perplexity import (
    CONTEXTUALIZED,
    FLAT,
    PerplexityEmbedder,
    windows,
)
from pinecall.providers.embed.wire import FLOATS, SIGNED_BYTES
from pinecall.providers.embedder import DIMENSIONS, Embedder, EmbedderUnreachable, WrongWidth

pytestmark = pytest.mark.unit

BASE = "https://api.perplexity.test/v1"
CONTEXT_MODEL = "pplx-embed-context-v1-4b"
FLAT_MODEL = "pplx-embed-v1-0.6b"

# What either door answers a chunk with, unless a test asks for another width.
A_VECTOR = [3] * DIMENSIONS


def an_embedder(
    model: str = CONTEXT_MODEL,
    *,
    answer: Callable[[httpx.Request], httpx.Response] | None = None,
    vendor: str = "Perplexity",
    encoding: str = SIGNED_BYTES,
) -> tuple[PerplexityEmbedder, list[httpx.Request]]:
    """A Perplexity that answers both doors from a transport that opens no socket."""
    seen: list[httpx.Request] = []

    def served(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if answer is not None:
            return answer(request)
        return _an_answer(request)

    embedder = PerplexityEmbedder(
        vendor=vendor,
        base_url=BASE,
        model=model,
        key="pplx-dead-sentinel",
        http=httpx.AsyncClient(transport=httpx.MockTransport(served)),
        encoding=encoding,
    )
    return embedder, seen


def _an_answer(request: httpx.Request, width: int = DIMENSIONS) -> httpx.Response:
    """The shape each door answers with: floats flat, base64 int8 one level deeper."""
    said: Any = json.loads(request.content)
    if request.url.path.endswith(CONTEXTUALIZED):
        rows = [
            {"data": [{"embedding": _as_base64_int8([3] * width)} for _chunk in document]}
            for document in said["input"]
        ]
        return httpx.Response(200, json={"data": rows})
    rows = [_an_embedding(said["encoding_format"], width) for _text in said["input"]]
    return httpx.Response(200, json={"data": [{"embedding": row} for row in rows]})


def _an_embedding(encoding: str, width: int) -> Any:
    """What the flat door answers in the encoding the request named."""
    return [0.5] * width if encoding == FLOATS else _as_base64_int8([3] * width)


def _as_base64_int8(values: Sequence[int]) -> str:
    """What the contextual door sends: signed bytes, base64, one per dimension."""
    return base64.b64encode(array("b", list(values)).tobytes()).decode("ascii")


def _sent(request: httpx.Request) -> Any:
    return json.loads(request.content)


async def test_the_flat_door_asks_for_the_encoding_this_vendor_takes_and_never_sniffs_it() -> None:
    """api.perplexity.ai refuses `float` outright; OpenRouter's mirror of the model answers it."""
    embedder, seen = an_embedder(FLAT_MODEL)
    vectors = await embedder.embed(["hola", "turno"])
    assert [len(vector) for vector in vectors] == [DIMENSIONS, DIMENSIONS]
    assert _sent(seen[0]) == {
        "model": FLAT_MODEL,
        "input": ["hola", "turno"],
        "encoding_format": "base64_int8",
    }

    openrouter, asked = an_embedder(FLAT_MODEL, vendor="OpenRouter", encoding=FLOATS)
    floats = await openrouter.embed(["hola"])
    assert len(floats[0]) == DIMENSIONS
    assert _sent(asked[0])["encoding_format"] == "float"
    assert [request.url.path for request in asked] == [f"/v1{FLAT}"]


async def test_every_door_stores_at_unit_length_because_every_one_answers_unnormalised() -> None:
    """MEASURED: 824 long contextual, 746 from Perplexity's flat door, 9 from OpenRouter's."""
    flat, _seen = an_embedder(FLAT_MODEL, vendor="OpenRouter", encoding=FLOATS)
    [vector] = await flat.embed(["hola"])
    assert math.isclose(sum(value * value for value in vector), 1.0, rel_tol=1e-9)


async def test_the_contextual_door_asks_for_int8_at_1024() -> None:
    """The 4b answers 2560 wide unless it is asked for the columns' width: it is always asked."""
    embedder, seen = an_embedder()
    await embedder.embed_documents([["uno", "dos", "tres"]])
    assert [request.url.path for request in seen] == [f"/v1{CONTEXTUALIZED}"]
    assert _sent(seen[0]) == {
        "model": CONTEXT_MODEL,
        "input": [["uno", "dos", "tres"]],
        "encoding_format": "base64_int8",
        "dimensions": DIMENSIONS,
    }


async def test_a_contextual_vector_is_decoded_as_signed_bytes_and_stored_at_unit_length() -> None:
    """1024 threes decode to 1024 threes, and every one of them lands at 1/sqrt(1024)."""
    embedder, _seen = an_embedder()
    [[vector]] = await embedder.embed_documents([["uno"]])
    assert len(vector) == DIMENSIONS
    assert math.isclose(sum(value * value for value in vector), 1.0, rel_tol=1e-9)
    assert math.isclose(vector[0], 1.0 / math.sqrt(DIMENSIONS), rel_tol=1e-9)


async def test_a_signed_byte_is_read_as_signed_and_never_as_an_unsigned_one() -> None:
    """-1 is 0xff on the wire; read unsigned it is 255, and the vector points somewhere else."""

    def minus_one(request: httpx.Request) -> httpx.Response:
        rows = [{"data": [{"embedding": _as_base64_int8([-1] * DIMENSIONS)}]}]
        assert request is not None
        return httpx.Response(200, json={"data": rows})

    embedder, _seen = an_embedder(answer=minus_one)
    [[vector]] = await embedder.embed_documents([["uno"]])
    assert math.isclose(vector[0], -1.0 / math.sqrt(DIMENSIONS), rel_tol=1e-9)


async def test_one_list_per_document_comes_back_in_the_order_the_documents_went_out() -> None:
    embedder, seen = an_embedder()
    documents = await embedder.embed_documents([["uno"], ["dos", "tres"], []])
    assert [len(vectors) for vectors in documents] == [1, 2, 0]
    assert [_sent(request)["input"] for request in seen] == [[["uno"]], [["dos", "tres"]]]


async def test_a_document_longer_than_the_context_goes_out_in_windows_of_it() -> None:
    """A window is measured in TOKENS, because the door counts the concatenation, not the chunks."""
    embedder, seen = an_embedder()
    long = ["palabra " * 8_000, "palabra " * 8_000, "palabra " * 8_000]
    [vectors] = await embedder.embed_documents([long])
    assert len(vectors) == 3
    assert [len(_sent(request)["input"][0]) for request in seen] == [2, 1]


async def test_a_query_is_a_document_of_one_chunk_so_it_shares_the_chunks_space() -> None:
    embedder, seen = an_embedder()
    [vector] = await embedder.embed(["¿cuánto cuesta la revisión?"])
    assert len(vector) == DIMENSIONS
    assert [request.url.path for request in seen] == [f"/v1{CONTEXTUALIZED}"]
    assert _sent(seen[0])["input"] == [["¿cuánto cuesta la revisión?"]]


async def test_a_flat_embedder_answers_embed_documents_by_flattening_and_cutting_back() -> None:
    embedder, seen = an_embedder(FLAT_MODEL)
    documents = await embedder.embed_documents([["uno"], ["dos", "tres"]])
    assert [len(vectors) for vectors in documents] == [1, 2]
    assert [_sent(request)["input"] for request in seen] == [["uno", "dos", "tres"]]


async def test_nothing_to_embed_asks_nobody() -> None:
    embedder, seen = an_embedder()
    assert await embedder.embed([]) == []
    assert await embedder.embed_documents([]) == []
    assert seen == []


async def test_a_reply_of_another_width_is_refused_by_the_model_that_answered_it() -> None:
    embedder, _seen = an_embedder(answer=lambda request: _an_answer(request, width=768))
    with pytest.raises(WrongWidth, match=f"{CONTEXT_MODEL} answers 768-wide vectors"):
        await embedder.embed_documents([["uno"]])


async def test_a_reply_that_mixes_two_widths_is_refused_naming_both() -> None:
    """One matrix cannot hold two widths, and nothing downstream would ever say which row broke."""

    def mixed(request: httpx.Request) -> httpx.Response:
        assert request is not None
        rows = [
            {
                "data": [
                    {"embedding": _as_base64_int8(A_VECTOR)},
                    {"embedding": _as_base64_int8([3] * 512)},
                ]
            }
        ]
        return httpx.Response(200, json={"data": rows})

    embedder, _seen = an_embedder(answer=mixed)
    with pytest.raises(WrongWidth, match="answers 512 and 1024-wide vectors"):
        await embedder.embed_documents([["uno", "dos"]])


async def test_a_refused_request_names_the_vendor_the_door_and_the_endpoints_own_words() -> None:
    """`Invalid model` on the terminal, never `Internal Server Error` and never a bare 400."""

    def refused(request: httpx.Request) -> httpx.Response:
        assert request is not None
        return httpx.Response(400, json={"error": {"message": "Invalid model", "type": "bad"}})

    embedder, _seen = an_embedder(FLAT_MODEL, answer=refused)
    with pytest.raises(EmbedderUnreachable) as raised:
        await embedder.embed(["hola"])
    assert str(raised.value) == (f"Perplexity at {BASE}{FLAT} did not answer: Invalid model")


async def test_a_refusal_with_no_sentence_in_it_falls_back_to_the_status() -> None:
    embedder, _seen = an_embedder(
        FLAT_MODEL, answer=lambda _request: httpx.Response(502, text="upstream")
    )
    with pytest.raises(EmbedderUnreachable, match="did not answer: HTTP 502"):
        await embedder.embed(["hola"])


async def test_a_connection_refused_is_unreachable_by_name_and_url_like_teis() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    embedder, _seen = an_embedder(FLAT_MODEL, answer=refuse, vendor="OpenRouter")
    with pytest.raises(EmbedderUnreachable, match=f"OpenRouter at {BASE}{FLAT} did not answer"):
        await embedder.embed(["hola"])


async def test_a_reply_with_fewer_vectors_than_chunks_is_refused_before_a_row_is_written() -> None:
    """Silently short, the vectors would be attached to the wrong chunks and no index would say."""

    def one_short(request: httpx.Request) -> httpx.Response:
        assert request is not None
        return httpx.Response(
            200, json={"data": [{"data": [{"embedding": _as_base64_int8(A_VECTOR)}]}]}
        )

    embedder, _seen = an_embedder(answer=one_short)
    with pytest.raises(EmbedderUnreachable, match="1 vectors for 2 chunks"):
        await embedder.embed_documents([["uno", "dos"]])


async def test_the_model_is_the_configured_one_and_nothing_is_asked_of_the_vendor() -> None:
    embedder, seen = an_embedder()
    assert await embedder.model() == CONTEXT_MODEL
    assert seen == []


def test_perplexity_is_an_embedder_of_the_declared_width() -> None:
    embedder: Embedder = an_embedder()[0]
    assert embedder.dimensions == DIMENSIONS


def test_a_window_holds_chunks_while_their_tokens_fit_and_a_long_one_goes_alone() -> None:
    assert list(windows(["uno dos", "tres", "cuatro"], 10)) == [["uno dos", "tres", "cuatro"]]
    assert list(windows(["uno dos tres", "cuatro cinco"], 3)) == [
        ["uno dos tres"],
        ["cuatro cinco"],
    ]
    assert list(windows([], 10)) == []

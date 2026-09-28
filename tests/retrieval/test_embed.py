"""The box's embedder: two wire shapes, batched by tokens, retried, its reply held to account."""

import math
from collections.abc import AsyncIterator

import httpx
import pytest

from pinecall.domain.errors import EmbedderUnreachable, NotAvailable, WrongWidth
from pinecall.providers.catalog import Embedding
from pinecall.retrieval.embed import PUSH_TIMEOUT, Embedder, estimated_tokens, halfvec
from tests.fakes.embeddings import TOO_BIG, Embeddings, int8_vector

URL = "https://api.perplexity.test/v1"
KEY = "a-key-of-the-box"
CONTEXTUAL = Embedding(vendor="perplexity", url=URL, model="embed-context-4b", shape="contextual")
FLAT = Embedding(vendor="openrouter", url=URL, model="embed-flat-06b", shape="embeddings")


@pytest.fixture
def vendor() -> Embeddings:
    """The vendor the embedder asks, answering well unless a test scripts otherwise."""
    return Embeddings()


@pytest.fixture
async def client(vendor: Embeddings) -> AsyncIterator[httpx.AsyncClient]:
    """The box's HTTP client, reaching only the fake vendor."""
    async with httpx.AsyncClient(transport=vendor.transport()) as http:
        yield http


@pytest.fixture
def slept() -> list[float]:
    """Every wait the embedder took, in seconds, instead of taking it."""
    return []


def an_embedder(
    client: httpx.AsyncClient, slept: list[float], embedding: Embedding = CONTEXTUAL
) -> Embedder:
    """The embedder on the fake vendor, its waits recorded and never slept."""

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    return Embedder(embedding, KEY, client, sleep=sleep)


def is_unit(vector: list[float]) -> bool:
    return math.isclose(sum(value * value for value in vector), 1.0, rel_tol=1e-9)


def refused(status: int, message: str, **headers: str) -> httpx.Response:
    """A vendor's refusal in its own words."""
    return httpx.Response(status, json={"error": {"message": message}}, headers=headers)


def flat_reply(*rows: dict[str, object]) -> httpx.Response:
    return httpx.Response(200, json={"data": list(rows)})


def a_row(index: int | None, value: float, width: int = 1024) -> dict[str, object]:
    row: dict[str, object] = {"embedding": [value] * width}
    if index is not None:
        row["index"] = index
    return row


# ── the request ──


async def test_the_embeddings_shape_asks_for_floats_at_its_width_and_never_sniffs_the_reply(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vectors = await an_embedder(client, slept, FLAT).embed(["hola", "turno"])
    assert [len(vector) for vector in vectors] == [1024, 1024]
    assert [request.url.path for request in vendor.requests] == ["/v1/embeddings"]
    assert vendor.sent() == [
        {
            "model": "embed-flat-06b",
            "input": ["hola", "turno"],
            "encoding_format": "float",
            "dimensions": 1024,
        }
    ]


async def test_the_contextual_shape_asks_for_signed_bytes_at_1024(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    await an_embedder(client, slept).embed_documents([["uno", "dos", "tres"]])
    assert [request.url.path for request in vendor.requests] == ["/v1/contextualizedembeddings"]
    assert vendor.sent() == [
        {
            "model": "embed-context-4b",
            "input": [["uno", "dos", "tres"]],
            "encoding_format": "base64_int8",
            "dimensions": 1024,
        }
    ]


async def test_the_rows_url_is_asked_whatever_its_trailing_slash(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    slashed = FLAT.model_copy(update={"url": f"{URL}/"})
    await an_embedder(client, slept, slashed).embed(["hola"])
    assert str(vendor.requests[0].url) == f"{URL}/embeddings"


@pytest.mark.parametrize("key", [KEY, {"api_key": KEY}])
async def test_the_boxs_key_travels_as_a_bearer_whether_the_row_holds_it_bare_or_named(
    vendor: Embeddings, client: httpx.AsyncClient, key: str | dict[str, str]
) -> None:
    await Embedder(FLAT, dict(key) if isinstance(key, dict) else key, client).embed(["hola"])
    assert vendor.requests[0].headers["authorization"] == f"Bearer {KEY}"


async def test_credentials_that_hold_no_api_key_are_refused_naming_the_vendor(
    client: httpx.AsyncClient,
) -> None:
    with pytest.raises(NotAvailable, match="openrouter hold no api_key"):
        Embedder(FLAT, {"token": KEY}, client)


async def test_the_model_and_the_width_are_the_rows_and_nothing_is_asked_of_the_vendor(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    embedder = an_embedder(client, slept)
    assert (embedder.embedding.model, embedder.embedding.dimensions) == ("embed-context-4b", 1024)
    assert vendor.requests == []


@pytest.mark.parametrize("embedding", [FLAT, CONTEXTUAL], ids=["embeddings", "contextual"])
async def test_nothing_to_embed_asks_nobody(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float], embedding: Embedding
) -> None:
    embedder = an_embedder(client, slept, embedding)
    assert await embedder.embed([]) == []
    assert await embedder.embed_documents([]) == []
    assert await embedder.embed_documents([[]]) == [[]]
    assert vendor.requests == []


@pytest.mark.parametrize("embedding", [FLAT, CONTEXTUAL], ids=["embeddings", "contextual"])
async def test_a_push_waits_long_for_a_window_the_vendor_takes_time_over(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float], embedding: Embedding
) -> None:
    await an_embedder(client, slept, embedding).embed_documents([["uno", "dos"]])
    assert vendor.requests[0].extensions["timeout"] == PUSH_TIMEOUT.as_dict()
    assert PUSH_TIMEOUT.read == 120.0


@pytest.mark.parametrize("embedding", [FLAT, CONTEXTUAL], ids=["embeddings", "contextual"])
async def test_a_query_keeps_the_clients_own_wait_and_the_turns_budget_bounds_it(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float], embedding: Embedding
) -> None:
    await an_embedder(client, slept, embedding).embed(["hola"])
    assert vendor.requests[0].extensions["timeout"] == client.timeout.as_dict()
    assert vendor.requests[0].extensions["timeout"]["read"] != 120.0


# ── documents, queries, windows and batches ──


async def test_one_list_per_document_comes_back_in_the_order_the_documents_went_out(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    documents = await an_embedder(client, slept).embed_documents([["uno"], ["dos", "tres"], []])
    assert [len(vectors) for vectors in documents] == [1, 2, 0]
    assert [body["input"] for body in vendor.sent()] == [[["uno"]], [["dos", "tres"]]]


async def test_a_query_is_a_document_of_one_chunk_so_it_shares_the_chunks_space(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    [vector] = await an_embedder(client, slept).embed(["¿cuánto cuesta la revisión?"])
    assert len(vector) == 1024
    assert [request.url.path for request in vendor.requests] == ["/v1/contextualizedembeddings"]
    assert vendor.sent()[0]["input"] == [["¿cuánto cuesta la revisión?"]]


async def test_the_embeddings_shape_answers_documents_by_flattening_and_cutting_back(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    documents = await an_embedder(client, slept, FLAT).embed_documents([["uno"], ["dos", "tres"]])
    assert [len(vectors) for vectors in documents] == [1, 2]
    assert [body["input"] for body in vendor.sent()] == [["uno", "dos", "tres"]]


async def test_a_document_longer_than_the_context_goes_out_in_windows_of_it(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    # 8 000 words are 10 400 tokens: two fit a 24 000-token window, the third does not.
    long = ["palabra " * 8_000] * 3
    [vectors] = await an_embedder(client, slept).embed_documents([long])
    assert len(vectors) == 3
    assert [len(texts) for texts in vendor.inputs()] == [2, 1]


async def test_a_window_holds_chunks_while_their_tokens_fit_and_a_long_one_goes_alone(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    embedder = an_embedder(client, slept)
    await embedder.embed_documents([["uno dos", "tres", "cuatro"]])
    await embedder.embed_documents([["palabra " * 20_000, "cuatro cinco"]])
    assert [body["input"] for body in vendor.sent()] == [
        [["uno dos", "tres", "cuatro"]],
        [["palabra " * 20_000]],
        [["cuatro cinco"]],
    ]


async def test_texts_are_batched_by_their_tokens_and_never_by_their_count(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    embedder = an_embedder(client, slept, FLAT)
    await embedder.embed(["hola"] * 500)
    # 40 000 words are 52 000 tokens: two of them are past a request's 100 000.
    await embedder.embed(["palabra " * 40_000] * 3)
    assert [len(texts) for texts in vendor.inputs()] == [500, 1, 1, 1]


def test_a_text_is_estimated_at_its_words_times_one_point_three() -> None:
    assert estimated_tokens("una dos tres cuatro cinco seis siete ocho nueve diez") == 13
    assert estimated_tokens("") == 0


# ── the reply ──


async def test_every_shape_answers_unit_vectors_because_neither_answers_them_normalised(
    client: httpx.AsyncClient, slept: list[float]
) -> None:
    [flat] = await an_embedder(client, slept, FLAT).embed(["hola"])
    [[contextual]] = await an_embedder(client, slept).embed_documents([["uno"]])
    assert is_unit(flat)
    assert is_unit(contextual)


async def test_a_contextual_vector_is_decoded_as_signed_bytes_and_stored_at_unit_length(
    client: httpx.AsyncClient, slept: list[float]
) -> None:
    [[vector]] = await an_embedder(client, slept).embed_documents([["uno"]])
    assert len(vector) == 1024
    assert is_unit(vector)
    assert math.isclose(vector[0], 1 / math.sqrt(1024), rel_tol=1e-9)


async def test_a_signed_byte_is_read_as_signed_and_never_as_an_unsigned_one(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.byte = -1
    [[vector]] = await an_embedder(client, slept).embed_documents([["uno"]])
    assert math.isclose(vector[0], -1 / math.sqrt(1024), rel_tol=1e-9)


async def test_a_zero_vector_has_no_direction_and_is_kept_as_it_came(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.value = 0.0
    [vector] = await an_embedder(client, slept, FLAT).embed(["hola"])
    assert vector == [0.0] * 1024


async def test_floats_where_signed_bytes_were_asked_for_are_refused_and_never_guessed_at(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [httpx.Response(200, json={"data": [{"data": [{"embedding": [0.5] * 1024}]}]})]
    with pytest.raises(EmbedderUnreachable, match="floats where base64_int8 was asked for"):
        await an_embedder(client, slept).embed_documents([["uno"]])


async def test_bytes_where_floats_were_asked_for_are_refused_and_never_guessed_at(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [flat_reply({"embedding": int8_vector([3] * 1024)})]
    with pytest.raises(EmbedderUnreachable, match="a base64 vector where floats were asked for"):
        await an_embedder(client, slept, FLAT).embed(["hola"])


async def test_a_reply_of_another_width_is_refused_by_the_model_that_answered_it(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.width = 768
    with pytest.raises(WrongWidth, match="embed-context-4b answers 768-wide vectors"):
        await an_embedder(client, slept).embed_documents([["uno"]])


async def test_a_reply_that_mixes_two_widths_is_refused_naming_both(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [flat_reply(a_row(0, 0.5), a_row(1, 0.5, width=512))]
    with pytest.raises(WrongWidth, match="answers 512 and 1024-wide vectors"):
        await an_embedder(client, slept, FLAT).embed(["uno", "dos"])


async def test_a_reply_with_fewer_vectors_than_chunks_is_refused_before_a_row_is_written(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    item = {"data": [{"data": [{"embedding": int8_vector([3] * 1024)}]}]}
    vendor.script = [httpx.Response(200, json=item)]
    with pytest.raises(EmbedderUnreachable, match="1 vectors for 2 chunks"):
        await an_embedder(client, slept).embed_documents([["uno", "dos"]])


async def test_rows_that_say_their_index_are_put_back_in_that_order(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [flat_reply(a_row(1, -0.5), a_row(0, 0.5))]
    first, second = await an_embedder(client, slept, FLAT).embed(["uno", "dos"])
    assert (first[0] > 0, second[0] < 0) == (True, True)


async def test_rows_that_say_no_index_are_read_in_the_order_they_came(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [flat_reply(a_row(None, -0.5), a_row(None, 0.5))]
    first, second = await an_embedder(client, slept, FLAT).embed(["uno", "dos"])
    assert (first[0] < 0, second[0] > 0) == (True, True)


async def test_an_index_set_that_is_not_every_position_once_is_refused_however_tidy(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [flat_reply(a_row(0, 0.5), a_row(0, 0.5), a_row(2, 0.5))]
    with pytest.raises(EmbedderUnreachable, match=r"indexed \[0, 0, 2\], not 0 to 2"):
        await an_embedder(client, slept, FLAT).embed(["uno", "dos", "tres"])


# ── refusals, retries and splits ──


async def test_a_refused_request_names_the_vendor_the_door_and_the_endpoints_own_words(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [refused(400, "Invalid model")]
    with pytest.raises(EmbedderUnreachable) as raised:
        await an_embedder(client, slept, FLAT).embed(["hola"])
    assert str(raised.value) == f"openrouter at {URL}/embeddings did not answer: Invalid model"


async def test_a_refusal_with_no_sentence_in_it_falls_back_to_the_status(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [httpx.Response(418, text="teapot")]
    with pytest.raises(EmbedderUnreachable, match="did not answer: HTTP 418"):
        await an_embedder(client, slept, FLAT).embed(["hola"])


async def test_a_200_that_carries_an_error_is_a_refusal_in_its_own_words(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [refused(200, "No endpoints found for embed-flat-06b")]
    with pytest.raises(EmbedderUnreachable, match="did not answer: No endpoints found"):
        await an_embedder(client, slept, FLAT).embed(["hola"])


async def test_a_refusal_about_size_is_split_in_halves_and_answered_in_order(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.too_big_over = 2
    vectors = await an_embedder(client, slept, FLAT).embed(["uno", "dos", "tres", "cuatro"])
    assert len(vectors) == 4
    assert [body["input"] for body in vendor.sent()] == [
        ["uno", "dos", "tres", "cuatro"],
        ["uno", "dos"],
        ["tres", "cuatro"],
    ]


async def test_a_contextual_window_refused_for_its_size_is_split_too(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [refused(200, TOO_BIG)]
    [vectors] = await an_embedder(client, slept).embed_documents([["uno", "dos"]])
    assert len(vectors) == 2
    assert [body["input"] for body in vendor.sent()] == [[["uno", "dos"]], [["uno"]], [["dos"]]]


async def test_a_refusal_about_size_is_split_five_times_at_most_then_raised(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.too_big_over = 0
    with pytest.raises(EmbedderUnreachable, match="exceeds maximum"):
        await an_embedder(client, slept, FLAT).embed(["hola"] * 64)
    assert [len(texts) for texts in vendor.inputs()] == [64, 32, 16, 8, 4, 2]


async def test_a_refusal_about_the_key_is_raised_at_once_and_never_split(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [refused(401, "Invalid API key")]
    with pytest.raises(EmbedderUnreachable, match="Invalid API key"):
        await an_embedder(client, slept, FLAT).embed(["uno", "dos", "tres", "cuatro"])
    assert len(vendor.requests) == 1
    assert slept == []


async def test_a_transient_failure_is_retried_after_a_wait_that_grows(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [httpx.Response(503), httpx.Response(502)]
    [vector] = await an_embedder(client, slept, FLAT).embed(["hola"])
    assert len(vector) == 1024
    assert len(vendor.requests) == 3
    first, second = slept
    assert 0.375 <= first <= 0.5
    assert 0.75 <= second <= 1.0


async def test_an_embedder_that_answers_5xx_three_times_is_unreachable_after_two_retries(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [httpx.Response(503)] * 3
    with pytest.raises(EmbedderUnreachable, match="did not answer: HTTP 503"):
        await an_embedder(client, slept, FLAT).embed(["hola"])
    assert len(vendor.requests) == 3
    assert len(slept) == 2


async def test_a_connection_refused_is_retried_then_unreachable_by_name_and_url(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [httpx.ConnectError("connection refused")] * 3
    with pytest.raises(EmbedderUnreachable, match=f"openrouter at {URL}/embeddings did not answer"):
        await an_embedder(client, slept, FLAT).embed(["hola"])
    assert len(vendor.requests) == 3


async def test_a_rate_limit_that_says_when_to_come_back_is_obeyed(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [refused(429, "slow down", **{"Retry-After": "2"})]
    await an_embedder(client, slept, FLAT).embed(["hola"])
    assert slept == [2.0]


async def test_a_wait_asked_in_milliseconds_is_read_before_one_in_seconds(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [refused(429, "slow down", **{"retry-after-ms": "1500", "Retry-After": "2"})]
    await an_embedder(client, slept, FLAT).embed(["hola"])
    assert slept == [1.5]


async def test_a_wait_of_an_hour_is_a_misconfiguration_and_is_not_waited(
    vendor: Embeddings, client: httpx.AsyncClient, slept: list[float]
) -> None:
    vendor.script = [refused(429, "slow down", **{"Retry-After": "3600"})]
    await an_embedder(client, slept, FLAT).embed(["hola"])
    [waited] = slept
    assert waited <= 0.5


# ── halfvec ──


def test_a_vector_that_half_precision_holds_exactly_reads_back_the_same() -> None:
    assert halfvec([1.0, 0.5, -0.25, 0.0]) == "[1.0,0.5,-0.25,0.0]"


def test_a_value_half_precision_cannot_hold_is_written_as_the_half_it_becomes() -> None:
    assert halfvec([3.14159265]) == "[3.140625]"
    assert halfvec([0.1]) == "[0.0999755859375]"


def test_the_edges_of_half_precision_are_what_the_column_would_hold() -> None:
    assert halfvec([65504.0, -0.0, 1e-8]) == "[65504.0,-0.0,0.0]"

"""A refused answer as one sentence: the `detail` a gateway wrote, or the body as it came."""

import pytest

from pinecall.types.refused_answer import refusal_detail

pytestmark = pytest.mark.unit


def test_the_detail_of_a_fastapi_refusal_is_the_sentence() -> None:
    assert refusal_detail('{"detail": "this key opens no org"}') == "this key opens no org"


def test_a_status_line_before_the_json_is_skipped() -> None:
    said = 'gateway said 403: {"detail": "another org\'s call"}'
    assert refusal_detail(said) == "another org's call"


@pytest.mark.parametrize(
    "said",
    [
        "Bad Gateway",
        "{not json at all",
        '["a", "list"]',
        '{"error": "no detail here"}',
        '{"detail": ""}',
        '{"detail": 42}',
    ],
)
def test_anything_without_a_sentence_in_detail_is_said_as_it_came(said: str) -> None:
    assert refusal_detail(said) == said

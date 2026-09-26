"""One reading of a declared language: its base code, however a person wrote it."""

import pytest

from pinecall.providers.language import primary

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("written", "read"),
    [("es", "es"), ("es-ES", "es"), ("en_US", "en"), ("spanish", "es"), (" EN ", "en")],
)
def test_every_spelling_of_a_language_is_its_base_code(written: str, read: str) -> None:
    assert primary(written) == read


def test_no_language_is_none_and_not_an_empty_word() -> None:
    assert primary(None) is None
    assert primary("") is None
    assert primary("   ") is None

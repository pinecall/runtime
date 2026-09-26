"""The test embedder holds to what the real one promises: the width, the norm, the neighbours."""

import math

import pytest

from pinecall.providers.embedder import DIMENSIONS, Embedder
from pinecall_testkit.vectors import HASH_MODEL, HashEmbedder, a_vector, cosine

pytestmark = pytest.mark.unit


def test_the_hash_embedder_is_an_embedder_of_the_declared_width() -> None:
    embedder: Embedder = HashEmbedder()
    assert embedder.dimensions == DIMENSIONS


async def test_the_hash_embedder_names_a_model_no_vendor_would() -> None:
    assert await HashEmbedder().model() == HASH_MODEL


async def test_the_same_words_give_the_same_unit_vector_whatever_their_order() -> None:
    [one, other] = await HashEmbedder().embed(["turno con la doctora", "la doctora con turno"])
    assert one == other
    assert len(one) == DIMENSIONS
    assert math.isclose(math.sqrt(sum(value * value for value in one)), 1.0)


def test_texts_that_share_words_point_closer_than_texts_that_share_none() -> None:
    about_slots = a_vector("hay turno a las diez con la doctora")
    also_about_slots = a_vector("la doctora tiene turno a las once")
    about_prices = a_vector("la consulta cuesta cuarenta euros")
    assert cosine(about_slots, also_about_slots) > cosine(about_slots, about_prices)


def test_a_text_with_no_words_is_still_a_unit_vector() -> None:
    empty = a_vector("   ...  ")
    assert math.isclose(math.sqrt(sum(value * value for value in empty)), 1.0)

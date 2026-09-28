"""The box's configuration is one row the console writes, seeded once, never read from code."""

import pytest
from pydantic import ValidationError

from pinecall.domain.errors import Conflict, DeclarationRefused, NotAvailable
from pinecall.postgres.pool import Pool
from pinecall.providers.catalog import (
    Embedding,
    Providers,
    Stage,
    Told,
    checked,
    configure,
    providers,
    seed,
)
from tests.conftest import postgres


@postgres
async def test_a_box_nobody_configured_says_how_to_configure_it(pool: Pool) -> None:
    with pytest.raises(NotAvailable, match="providers seed"):
        await providers(pool)


@postgres
async def test_the_seed_is_read_back_whole(pool: Pool, configured: Providers) -> None:
    await seed(pool, configured)
    assert await providers(pool) == configured


@postgres
async def test_a_second_seed_is_refused_and_the_first_kept(
    pool: Pool, configured: Providers
) -> None:
    await seed(pool, configured)
    with pytest.raises(Conflict, match="configured already"):
        await seed(pool, configured.model_copy(update={"hints": ("fr",)}))
    assert (await providers(pool)).hints == ("es", "en")


@postgres
async def test_what_the_console_writes_replaces_the_row_whole(
    pool: Pool, configured: Providers
) -> None:
    await seed(pool, configured)
    edited = configured.model_copy(update={"voices": {}, "hints": ("pt",)})
    await configure(pool, edited)
    assert await providers(pool) == edited


def test_a_default_nobody_installed_or_for_a_stage_it_does_not_do_is_refused_when_written(
    configured: Providers,
) -> None:
    ghost = configured.model_copy(
        update={"defaults": {**configured.defaults, "tts": Stage(vendor="nobody")}}
    )
    with pytest.raises(DeclarationRefused, match="no vendor named 'nobody'"):
        checked(ghost)
    deaf = configured.model_copy(
        update={"defaults": {**configured.defaults, "stt": Stage(vendor="anthropic")}}
    )
    with pytest.raises(DeclarationRefused, match="anthropic has no stt"):
        checked(deaf)


def test_a_key_of_the_row_names_a_stage_and_a_vendor_that_does_it(configured: Providers) -> None:
    with pytest.raises(DeclarationRefused, match="a stage is one of"):
        checked(configured.model_copy(update={"models": {"voice/cartesia": "sonic-3"}}))
    with pytest.raises(DeclarationRefused, match="anthropic has no tts"):
        checked(configured.model_copy(update={"tuning": {"tts/anthropic": Told()}}))
    assert checked(configured) == configured


@postgres
async def test_whatsapp_is_a_key_and_never_a_stage(pool: Pool, configured: Providers) -> None:
    chatting = configured.model_copy(
        update={"defaults": {**configured.defaults, "llm": Stage(vendor="whatsapp")}}
    )
    with pytest.raises(DeclarationRefused, match="no vendor named 'whatsapp'"):
        await seed(pool, chatting)


EMBEDDING = {
    "vendor": "an-embedder",
    "url": "https://embed.test/v1",
    "model": "embed-context-1",
    "shape": "contextual",
}


@postgres
async def test_the_embedder_is_read_back_with_the_row(pool: Pool, configured: Providers) -> None:
    embedding = Embedding.model_validate(EMBEDDING)
    await seed(pool, configured.model_copy(update={"embedding": embedding}))
    assert (await providers(pool)).embedding == embedding
    assert embedding.dimensions == 1024


def test_a_row_with_no_embedder_is_a_box_that_embeds_nothing(configured: Providers) -> None:
    assert configured.embedding is None


def test_an_embedder_of_another_width_is_refused_naming_the_column(configured: Providers) -> None:
    wide = Embedding.model_validate({**EMBEDDING, "dimensions": 2560})
    with pytest.raises(DeclarationRefused, match=r"halfvec\(1024\)"):
        checked(configured.model_copy(update={"embedding": wide}))


def test_a_wire_shape_nobody_speaks_is_refused_when_read() -> None:
    with pytest.raises(ValidationError, match="shape"):
        Embedding.model_validate({**EMBEDDING, "shape": "sparse"})

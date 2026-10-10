"""The box's configuration is one row the console writes, seeded once, never read from code."""

import pytest
from pydantic import ValidationError

from pinecall.domain.errors import Conflict, DeclarationRefused, NotAvailable
from pinecall.postgres.migrate import migration_files
from pinecall.postgres.pool import Pool
from pinecall.providers.catalog import (
    Embedding,
    Judge,
    Providers,
    Stage,
    StageOptions,
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
        checked(configured.model_copy(update={"tuning": {"tts/anthropic": StageOptions()}}))
    assert checked(configured) == configured


def test_a_tuning_key_may_name_a_model_and_only_a_model_takes_a_request(
    configured: Providers,
) -> None:
    disabled = StageOptions(request={"thinking": {"type": "disabled"}})
    by_model = {"tuning": {"llm/anthropic/claude-haiku-5-5": disabled}}
    assert checked(configured.model_copy(update=by_model)).tuning == by_model["tuning"]
    with pytest.raises(DeclarationRefused, match="no vendor named 'nobody'"):
        checked(configured.model_copy(update={"tuning": {"llm/nobody/a-model": disabled}}))
    with pytest.raises(DeclarationRefused, match="only llm"):
        checked(configured.model_copy(update={"tuning": {"stt/deepgram": disabled}}))


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


def test_a_default_may_name_who_takes_over_and_each_is_checked_as_it_is(
    configured: Providers,
) -> None:
    backed = configured.model_copy(
        update={
            "defaults": {
                **configured.defaults,
                "llm": Stage(vendor="anthropic", fallbacks=(Stage(vendor="openai"),)),
            }
        }
    )
    assert checked(backed) == backed
    ghost = backed.model_copy(
        update={
            "defaults": {
                **configured.defaults,
                "llm": Stage(vendor="anthropic", fallbacks=(Stage(vendor="deepgram"),)),
            }
        }
    )
    with pytest.raises(DeclarationRefused, match="deepgram has no llm"):
        checked(ghost)


def test_a_fallback_of_a_fallback_and_one_for_the_judge_are_refused(configured: Providers) -> None:
    deep = Stage(vendor="openai", fallbacks=(Stage(vendor="groq"),))
    nested = configured.model_copy(
        update={
            "defaults": {
                **configured.defaults,
                "llm": Stage(vendor="anthropic", fallbacks=(deep,)),
            }
        }
    )
    with pytest.raises(DeclarationRefused, match="names no fallbacks of its own"):
        checked(nested)
    judged = configured.model_copy(
        update={
            "judge": Judge(
                llm=Stage(vendor="anthropic", fallbacks=(Stage(vendor="openai"),)), ceiling_usd=0.1
            )
        }
    )
    with pytest.raises(DeclarationRefused, match="the judge runs on one model"):
        checked(judged)


def test_ears_that_take_over_end_the_turn_as_the_default_does(configured: Providers) -> None:
    # deepgram ends the turn itself on this row; soniox is not told to.
    mixed = configured.model_copy(
        update={
            "defaults": {
                **configured.defaults,
                "stt": Stage(vendor="deepgram", fallbacks=(Stage(vendor="soniox"),)),
            }
        }
    )
    with pytest.raises(DeclarationRefused, match="stt fallback soniox: it leaves the turn open"):
        checked(mixed)
    alike = mixed.model_copy(
        update={
            "tuning": {**mixed.tuning, "stt/soniox": StageOptions(ends_the_turn=True)},
        }
    )
    assert checked(alike) == alike


@postgres
async def test_v1_mini_written_only_as_the_default_gives_way_to_smart_turn(
    pool: Pool, configured: Providers
) -> None:
    spelled = StageOptions(turn_model="v1-mini")
    flux = StageOptions(builds="STTv2", ends_the_turn=True, turn_model="v1-mini")
    tuning = {**configured.tuning, "stt/cartesia": spelled, "stt/deepgram": flux}
    await seed(pool, configured.model_copy(update={"tuning": tuning}))
    migration = next(path for path in migration_files() if "smart_turn" in path.name)
    async with pool.connection() as connection:
        await connection.execute(migration.read_bytes())
    after = (await providers(pool)).tuning
    assert (after["stt/cartesia"].turn_model, after["stt/deepgram"].turn_model) == (None, None)
    assert after["stt/deepgram"].ends_the_turn

"""The box's providers configuration: one row in Postgres the operator edits from the console."""

from collections.abc import Iterable
from typing import Literal

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from pinecall.domain.errors import Conflict, DeclarationRefused, NotAvailable
from pinecall.domain.names import Json, JsonObject
from pinecall.postgres.pool import Pool
from pinecall.process import box_settings
from pinecall.providers.build import MODALITIES, Modality, TurnModel, doing

ROW = "providers"

SEED = """
INSERT INTO box_settings (name, value) VALUES (%(name)s, %(value)s)
ON CONFLICT (name) DO NOTHING
RETURNING name
"""

# The width of every halfvec column that holds a vector.
VECTOR_WIDTH = 1024

UNSET = (
    "this box has no providers configuration yet: the operator writes it from the console, or "
    "`pinecall-runtime providers seed` writes the one it starts from"
)


NO_JUDGE_FALLBACK = "the judge runs on one model: fallbacks are for the stages of a call"

NESTED = "{modality} fallback {vendor}: a fallback names no fallbacks of its own"

ASKED_OF_A_MODEL = "{key}: `request` is what a model is asked beside the conversation, so only llm"

TURN_ENDED_OTHERWISE = (
    "stt fallback {vendor}: it {how} and {primary} does the other; the session reads the end "
    "of the turn one way for the whole call, so every vendor of the stage must end it alike"
)


class Stage(BaseModel):
    """A vendor and, when not the plugin's own, its model; a default names who takes over."""

    model_config = ConfigDict(frozen=True)

    vendor: str
    model: str | None = None
    # A default stage's vendors in the order they take over when the one before fails.
    fallbacks: tuple["Stage", ...] = ()


class Rate(BaseModel):
    """What a model costs in US dollars: per million tokens, per character, second or minute."""

    model_config = ConfigDict(frozen=True)

    input: float | None = None
    output: float | None = None
    # Omitted, a cache read costs what fresh input does.
    cached_input: float | None = None
    cache_creation: float | None = None
    characters: float | None = None
    audio_seconds: float | None = None
    # A phone leg's minute, each one begun billed whole.
    minutes: float | None = None
    as_of: str = ""


class StageOptions(BaseModel):
    """What one vendor is told for one stage: which class it builds and the kwargs it is given."""

    model_config = ConfigDict(frozen=True)

    # The plugin's class where it is not the one named LLM, STT or TTS (`STTv2`).
    builds: str | None = None
    options: JsonObject = Field(default_factory=dict[str, Json])
    # The ears end the turn themselves, so the session stacks no detector on top.
    ends_the_turn: bool = False
    # Where they do not, which local model reads the end of the turn off the audio.
    turn_model: TurnModel = "v1-mini"
    # What every request of an llm stage carries beside the conversation, in the vendor's own
    # field names (`{"thinking": {"type": "disabled"}}`): livekit's extra_kwargs, each request.
    request: JsonObject = Field(default_factory=dict[str, Json])


class KeptVoice(BaseModel):
    """A voice the row lists for a vendor, as a picker shows it: its id and what to choose by."""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    gender: str = ""
    # Where the accent is from: `ES`, `MX`; a language code does not tell them apart.
    country: str = ""
    accent: str = ""
    description: str = ""


class Judge(BaseModel):
    """The model that judges a finished call, on the box's key, and what one call may spend."""

    model_config = ConfigDict(frozen=True)

    llm: Stage
    ceiling_usd: float


class Caller(BaseModel):
    """What one simulated call's caller may spend, its lines and its voice, before it hangs up."""

    model_config = ConfigDict(frozen=True)

    ceiling_usd: float = Field(gt=0)


class Embedding(BaseModel):
    """The one embedder this box runs, on its own key; every base and fact is in its space."""

    model_config = ConfigDict(frozen=True)

    # The box's `credentials/<vendor>` row holds its key.
    vendor: str
    url: str
    model: str
    # Two wire formats: `embeddings` takes texts, `contextual` takes documents of chunks.
    shape: Literal["embeddings", "contextual"]
    dimensions: int = VECTOR_WIDTH


class Providers(BaseModel):
    """The operator's choices: defaults, what each vendor is told, what a model costs, the judge."""

    model_config = ConfigDict(frozen=True)

    # The stage an agent runs where it names no vendor.
    defaults: dict[Modality, Stage]
    # `tts/elevenlabs`: the model a vendor named alone runs, where the plugin's own is not.
    models: dict[str, str] = Field(default_factory=dict[str, str])
    # `cartesia/es`: the voice an agent that names none speaks with.
    voices: dict[str, str] = Field(default_factory=dict[str, str])
    # `cartesia/es`: the voices a picker offers, where the vendor's plugin lists none of its own.
    listed: dict[str, tuple[KeptVoice, ...]] = Field(
        default_factory=dict[str, tuple[KeptVoice, ...]]
    )
    # `stt/deepgram`: what that vendor is told for that stage; `llm/anthropic/claude-haiku-5-5`
    # for the models whose id starts so, the longest such key replacing the vendor's whole.
    tuning: dict[str, StageOptions] = Field(default_factory=dict[str, StageOptions])
    # Languages the ears listen for beside the call's own.
    hints: tuple[str, ...] = ()
    # A model's price, by the longest prefix of its id: dated snapshots price by family.
    rates: dict[str, Rate] = Field(default_factory=dict[str, Rate])
    judge: Judge | None = None
    # Absent: a simulated caller spends what its turns ask, as before the row named one.
    caller: Caller | None = None
    # `es`: the line a voice reads in the picker.
    lines: dict[str, str] = Field(default_factory=dict[str, str])
    embedding: Embedding | None = None


def tuning_of(
    configured: Providers, modality: Modality, vendor: str, model: str | None
) -> StageOptions | None:
    """What a vendor is told for a stage: the longest key naming the model's id, else its own."""
    stage = f"{modality}/{vendor}"
    models = (
        key.removeprefix(f"{stage}/") for key in configured.tuning if key.startswith(f"{stage}/")
    )
    named = None if model is None else longest_prefix(models, model)
    return configured.tuning.get(stage if named is None else f"{stage}/{named}")


def longest_prefix(keys: Iterable[str], name: str) -> str | None:
    """The longest of the keys the name starts with, so a dated snapshot reads as its family."""
    listed = [key for key in keys if name.startswith(key)]
    return max(listed, key=len) if listed else None


def judge_ceiling(configured: Providers) -> float | None:
    """What one call may spend on the box's judge, or None where the row names no judge."""
    return None if configured.judge is None else configured.judge.ceiling_usd


async def providers(pool: Pool) -> Providers:
    """The box's configuration; NotAvailable until it was written."""
    async with pool.connection() as connection:
        value = await box_settings.read(connection, ROW)
    if value is None:
        raise NotAvailable(UNSET)
    return Providers.model_validate(value)


async def configure(pool: Pool, edited: Providers) -> None:
    """Write the configuration whole, as the console's box screen sends it."""
    checked(edited)
    async with pool.connection() as connection:
        await box_settings.write(connection, ROW, edited.model_dump(mode="json"))


async def seed(pool: Pool, seeded: Providers) -> None:
    """Write the configuration a box starts from; Conflict when one is already there."""
    checked(seeded)
    async with pool.connection() as connection:
        written = await connection.execute(
            SEED, {"name": ROW, "value": Jsonb(seeded.model_dump(mode="json"))}
        )
        if await written.fetchone() is None:
            raise Conflict("this box is configured already: the console edits what is there")


# A row that names a vendor nobody installed, or for a stage it does not do, is refused where it
# is written, not on the first call that reads it.
def checked(written: Providers) -> Providers:
    """The configuration: each vendor installed and doing its stage, the embedder at 1024 wide."""
    for modality, stage in written.defaults.items():
        doing(stage.vendor, modality)
        for fallback in stage.fallbacks:
            _a_fallback(written, modality, stage, fallback)
    for key in written.models:
        _a_stage_key(key)
    for key, options in written.tuning.items():
        if _a_stage_key(key) != "llm" and options.request:
            raise DeclarationRefused(ASKED_OF_A_MODEL.format(key=key))
    for key in (*written.voices, *written.listed):
        doing(key.partition("/")[0], "tts")
    if written.judge is not None:
        doing(written.judge.llm.vendor, "llm")
        if written.judge.llm.fallbacks:
            raise DeclarationRefused(NO_JUDGE_FALLBACK)
    if written.embedding is not None and written.embedding.dimensions != VECTOR_WIDTH:
        raise DeclarationRefused(
            f"the embedder answers {written.embedding.dimensions}-wide vectors; every vector "
            f"column is halfvec({VECTOR_WIDTH}), so it must be asked for {VECTOR_WIDTH}"
        )
    return written


# `stt/deepgram`, or `llm/anthropic/claude-haiku-5-5` where the key names a model of the vendor.
def _a_stage_key(key: str) -> Modality:
    modality, _, vendor = key.partition("/")
    if modality not in MODALITIES:
        raise DeclarationRefused(f"{key!r}: a stage is one of {', '.join(MODALITIES)}")
    doing(vendor.partition("/")[0], modality)
    return modality


# The ears are chosen once for the whole session: a fallback that ends the turn itself where the
# primary does not (or the reverse) would leave the call with nobody ending the caller's turn.
def _a_fallback(written: Providers, modality: Modality, primary: Stage, fallback: Stage) -> None:
    doing(fallback.vendor, modality)
    if fallback.fallbacks:
        raise DeclarationRefused(NESTED.format(modality=modality, vendor=fallback.vendor))
    if modality != "stt" or _ends_the_turn(written, fallback) == _ends_the_turn(written, primary):
        return
    how = "ends the turn itself" if _ends_the_turn(written, fallback) else "leaves the turn open"
    raise DeclarationRefused(
        TURN_ENDED_OTHERWISE.format(vendor=fallback.vendor, how=how, primary=primary.vendor)
    )


def _ends_the_turn(written: Providers, stage: Stage) -> bool:
    options = tuning_of(written, "stt", stage.vendor, stage.model)
    return options is not None and options.ends_the_turn

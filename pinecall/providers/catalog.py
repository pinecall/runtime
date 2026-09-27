"""The box's providers configuration: one row in Postgres the operator edits from the console."""

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from pinecall.domain.errors import Conflict, DeclarationRefused, NotAvailable
from pinecall.domain.types import Json, JsonObject
from pinecall.postgres.pool import Pool
from pinecall.providers.build import MODALITIES, Modality, doing
from pinecall.wire.parts import CostRate

ROW = "providers"

READ = "SELECT value FROM box_settings WHERE name = %(name)s"
WRITE = """
INSERT INTO box_settings (name, value) VALUES (%(name)s, %(value)s)
ON CONFLICT (name) DO UPDATE SET value = excluded.value, set_at = now()
"""
SEED = """
INSERT INTO box_settings (name, value) VALUES (%(name)s, %(value)s)
ON CONFLICT (name) DO NOTHING
RETURNING name
"""

UNSET = (
    "this box has no providers configuration yet: the operator writes it from the console, or "
    "`pinecall-runtime providers seed` writes the one it starts from"
)


class Stage(BaseModel):
    """A vendor and, when not the plugin's own, its model."""

    model_config = ConfigDict(frozen=True)

    vendor: str
    model: str | None = None


class Rate(BaseModel):
    """What a model costs in US dollars: per million tokens, per character, or per second heard."""

    model_config = ConfigDict(frozen=True)

    input: float | None = None
    output: float | None = None
    # Omitted, a cache read costs what fresh input does.
    cached_input: float | None = None
    cache_creation: float | None = None
    characters: float | None = None
    audio_seconds: float | None = None
    as_of: str = ""


class Told(BaseModel):
    """What one vendor is told for one stage: which class it builds and the kwargs it is given."""

    model_config = ConfigDict(frozen=True)

    # The plugin's class where it is not the one named LLM, STT or TTS (`STTv2`).
    builds: str | None = None
    options: JsonObject = Field(default_factory=dict[str, Json])
    # The ears end the turn themselves, so the session stacks no detector on top.
    ends_the_turn: bool = False


class Judge(BaseModel):
    """The model that judges a finished call, on the box's key, and what one call may spend."""

    model_config = ConfigDict(frozen=True)

    llm: Stage
    ceiling_eur: float


class Providers(BaseModel):
    """The operator's choices: defaults, what each vendor is told, what a model costs, the judge."""

    model_config = ConfigDict(frozen=True)

    # The stage an agent runs where it names no vendor.
    defaults: dict[Modality, Stage]
    # `tts/elevenlabs`: the model a vendor named alone runs, where the plugin's own is not.
    models: dict[str, str] = Field(default_factory=dict[str, str])
    # `cartesia/es`: the voice an agent that names none speaks with.
    voices: dict[str, str] = Field(default_factory=dict[str, str])
    # `stt/deepgram`: what that vendor is told for that stage.
    tuning: dict[str, Told] = Field(default_factory=dict[str, Told])
    # Languages the ears listen for beside the call's own.
    hints: tuple[str, ...] = ()
    # A model's price, by the longest prefix of its id: dated snapshots price by family.
    rates: dict[str, Rate] = Field(default_factory=dict[str, Rate])
    exchange: CostRate
    judge: Judge | None = None
    # `es`: the line a voice reads in the picker.
    lines: dict[str, str] = Field(default_factory=dict[str, str])


async def providers(pool: Pool) -> Providers:
    """The box's configuration; NotAvailable until it was written."""
    async with pool.connection() as connection:
        row = await (await connection.execute(READ, {"name": ROW})).fetchone()
    if row is None:
        raise NotAvailable(UNSET)
    return Providers.model_validate(row["value"])


async def configure(pool: Pool, edited: Providers) -> None:
    """Write the configuration whole, as the console's box screen sends it."""
    checked(edited)
    async with pool.connection() as connection:
        await connection.execute(WRITE, {"name": ROW, "value": _jsonb(edited)})


async def seed(pool: Pool, seeded: Providers) -> None:
    """Write the configuration a box starts from; Conflict when one is already there."""
    checked(seeded)
    async with pool.connection() as connection:
        written = await connection.execute(SEED, {"name": ROW, "value": _jsonb(seeded)})
        if await written.fetchone() is None:
            raise Conflict("this box is configured already: the console edits what is there")


# A row that names a vendor nobody installed, or for a stage it does not do, is refused where it
# is written, not on the first call that reads it.
def checked(written: Providers) -> Providers:
    """The configuration, every vendor it names installed and doing the stage it is named for."""
    for modality, stage in written.defaults.items():
        doing(stage.vendor, modality)
    for key in (*written.models, *written.tuning):
        modality, _, vendor = key.partition("/")
        if modality not in MODALITIES:
            raise DeclarationRefused(f"{key!r}: a stage is one of {', '.join(MODALITIES)}")
        doing(vendor, modality)
    for key in written.voices:
        doing(key.partition("/")[0], "tts")
    if written.judge is not None:
        doing(written.judge.llm.vendor, "llm")
    return written


def _jsonb(written: Providers) -> Jsonb:
    return Jsonb(written.model_dump(mode="json"))

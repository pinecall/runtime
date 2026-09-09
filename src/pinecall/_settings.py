"""Every environment variable the runtime reads, declared once, for both processes."""

from collections.abc import Iterator
from pathlib import Path
from typing import override

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

# Our own knobs carry this prefix; a vendor key keeps the vendor's own name (the alias on the
# field), so the SDK that reads ANTHROPIC_API_KEY by itself and this class agree.
ENV_PREFIX = "PINECALL_"

# The two names a .env is looked for under: in the directory the process started in, then in
# each parent up to the repository root. `uv run pinecall-runtime …` starts in the runtime
# directory, where the first name is the file; an app started from a checkout's root, or from an
# example directory deeper in it, finds the same file under the second. Where the walk stops with
# both names present, the later wins — pydantic-settings' own order for a list of files.
ENV_FILES: tuple[str, ...] = (".env", "runtime/.env")


class Settings(BaseSettings):
    """The environment, typed and frozen. One per process, built by load_settings()."""

    # A real environment variable WINS over the file: pydantic-settings reads the process
    # environment before the dotenv source. That is what a box with systemd's EnvironmentFile
    # needs, and what a laptop that exports a key from another project will feel — the export
    # shadows the file, and `env | grep PINECALL` is the first thing to run when it surprises.
    # extra="ignore" because the file may carry names this runtime does not read; an unknown key
    # is skipped, never an error at startup.
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    # ── LiveKit: the media plane both processes talk to ─────────────────────────
    livekit_url: str = Field(
        default="ws://127.0.0.1:7880",
        validation_alias="LIVEKIT_URL",
        description="LiveKit: the media plane both processes talk to. One port serves ws and http.",
    )
    livekit_api_key: str | None = Field(
        default=None,
        validation_alias="LIVEKIT_API_KEY",
        description="The LiveKit API key, as the LiveKit server's own config declares it.",
    )
    # The pair also signs and verifies a call token: it IS a LiveKit room token, so there is no
    # second secret to set. auth/scopes.py derives one from the dev key when this is unset.
    livekit_api_secret: str | None = Field(
        default=None,
        validation_alias="LIVEKIT_API_SECRET",
        description="Its secret. It also signs the call tokens, so there is no second one.",
    )
    # What POST /v1/tokens tells a browser to connect to. A box talks to its LiveKit on localhost
    # and a browser cannot; unset, the browser is told LIVEKIT_URL, which is right on a laptop.
    livekit_public_url: str | None = Field(
        default=None,
        validation_alias="LIVEKIT_PUBLIC_URL",
        description="The LiveKit URL a browser is told to join. Unset, it hears LIVEKIT_URL.",
    )

    # ── The services the doctor asks after: Postgres, and the embedder ─────────
    # Every default is what infra/compose/dev.yml serves, so a fresh clone runs the doctor with
    # no .env at all and every ✗ it prints is a service that is down, never a setting nobody set.
    database_url: str = Field(
        default="postgresql://pinecall:pinecall@127.0.0.1:5432/pinecall",
        validation_alias="DATABASE_URL",
        description="Postgres 17 with pgvector and pg_textsearch: the one stateful service.",
    )
    tei_url: str = Field(
        default="http://127.0.0.1:8081",
        validation_alias="TEI_URL",
        description="TEI, the embedder. 8081, because the gateway serves 8080 on the same host.",
    )

    # ── Provider keys, named exactly as each vendor's SDK names them ───────────
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias="ANTHROPIC_API_KEY",
        description="Anthropic, an LLM. Every provider key keeps the vendor's own variable name.",
    )
    openai_api_key: str | None = Field(
        default=None,
        validation_alias="OPENAI_API_KEY",
        description="OpenAI, the other LLM a call may run on.",
    )
    soniox_api_key: str | None = Field(
        default=None,
        validation_alias="SONIOX_API_KEY",
        description="Soniox, an STT. A call needs one key of each role: llm, stt, tts.",
    )
    deepgram_api_key: str | None = Field(
        default=None,
        validation_alias="DEEPGRAM_API_KEY",
        description="Deepgram, the other STT.",
    )
    eleven_api_key: str | None = Field(
        default=None,
        validation_alias="ELEVEN_API_KEY",
        description="ElevenLabs, the TTS.",
    )
    # Not a model vendor: the token the Graph API takes when a message goes back out. It sits
    # beside the others because an org may bring its own, and the registry reads both the same way.
    whatsapp_access_token: str | None = Field(
        default=None,
        validation_alias="WHATSAPP_ACCESS_TOKEN",
        description="The box's own WhatsApp Cloud API token, used for an org that brought none.",
    )

    # ── WhatsApp: the two secrets the webhook itself is guarded by ──────────────
    whatsapp_app_secret: str | None = Field(
        default=None,
        description=(
            "The Meta app's App Secret: every webhook body is HMAC-SHA256-signed with it. "
            "Unset, the WhatsApp door is closed."
        ),
    )
    whatsapp_verify_token: str | None = Field(
        default=None,
        description=(
            "The word Meta echoes back when the webhook is subscribed. Choose it; type it in "
            "the Meta app."
        ),
    )

    # ── Recordings: whether a call's audio is kept at all, and where ───────────
    # RECORD keeps its bare name because it is the one switch an operator flips on a box, and
    # pydantic already reads 0/false/no/off as no. worker/recordings.py composes the path.
    record: bool = Field(
        default=True,
        validation_alias="RECORD",
        description="Whether a call's audio is kept at all. 0, false, no and off all mean no.",
    )
    recordings_root: str = Field(
        default="recordings",
        validation_alias="PINECALL_RECORDINGS",
        description="Where a kept recording lands, absolute or relative to the working directory.",
    )

    # ── The worker: which gateway it asks, and on whose behalf ──────────────────
    # Read in the JOB process, never the parent: livekit runs a call in a process of its own and
    # hands it the entrypoint by name, so a flag parsed in the parent would not reach it and the
    # environment is the one thing the child inherits.
    gateway_url: str = Field(
        default="http://127.0.0.1:8080",
        description="The gateway a worker's job asks. Read in the job process, never the parent.",
    )
    agent: str | None = Field(
        default=None,
        description="The agent a job that names none is for.",
    )
    # An app socket id as the gateway minted it, which a job puts in the `app` of its call. A
    # developer's own worker sets it so the call is served by the process they typed the command
    # in; a fleet worker on a box sets none and takes the newest holder.
    app: str | None = Field(
        default=None,
        description="The app socket a job's call claims. Unset, the call takes the newest holder.",
    )

    # ── Ours: the keys and the knobs, each under PINECALL_ ──────────────────────
    ops_key: str | None = Field(
        default=None,
        description="The key /v1/ops/* is authenticated by. Unset, the operator API is closed.",
    )
    # The org's own key, as `pinecall-runtime keys issue` printed it: what the worker and the
    # tenant's app knock at the gateway with on a box that has a database.
    api_key: str | None = Field(
        default=None,
        description="The org's own API key, as `pinecall-runtime keys issue` printed it.",
    )
    # One API key that needs no database, so a clone runs the gateway before Postgres exists.
    # Set it and it is the ONLY key the gateway honours, and it opens NO Postgres pool at all —
    # so a box, which needs the routes and api_keys tables, never sets it. Development only.
    dev_key: str | None = Field(
        default=None,
        description="One API key that needs no database. Set it and it is the only one. Dev only.",
    )
    # The one secret that guards other people's secrets: a Fernet key, generated once on the box,
    # under which every tenant's own provider key is encrypted at rest. It lives here and never in
    # the database, so a stolen dump is not a stolen tenant. Unset, the provider-key doors are
    # closed and every call runs on the box's own vendor keys, which is what a laptop means.
    vault_key: str | None = Field(
        default=None,
        description=(
            "A Fernet key, generated once on the box by setup.sh: a tenant's own provider keys "
            "are encrypted under it. Unset, the provider-key doors answer 503."
        ),
    )
    log_level: str = Field(
        default="INFO",
        description="How much both processes say: DEBUG, INFO, WARNING or ERROR.",
    )
    # What judging ONE call at hang-up may cost. Zero closes the door on every judge that would
    # ask a model; the policies that answer by code still answer. One number per box: a judge's
    # budget is not a quota, and stays the operator's. See docs/decisions/scoring.md.
    judge_ceiling_eur: float = Field(
        default=0.002,
        description="What judging one call may spend on a model, in euros. Zero: no judge asks.",
    )

    # pydantic resolves an env_file NAME against the working directory alone, so it is the one
    # part of the config that cannot express the walk. The dotenv source is rebuilt here over the
    # absolute paths env_files_read() found; everything else about it — the prefix, the encoding,
    # its place AFTER the process environment — is still model_config's. When model_config carries
    # no env_file the default source is handed back untouched, which is how tests/conftest.py
    # keeps the suite off the operator's file by clearing that one key.
    @override
    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """The dotenv source reads the files the walk found, not two names under the cwd."""
        if settings_cls.model_config.get("env_file") is None:
            return init_settings, env_settings, dotenv_settings, file_secret_settings
        walked = DotEnvSettingsSource(settings_cls, env_file=env_files_read())
        return init_settings, env_settings, walked, file_secret_settings


def load_settings() -> Settings:
    """Read the environment now. Cheap, and no hidden global: hold the result where it is needed."""
    return Settings()


def env_files_read() -> list[Path]:
    """The .env files a Settings built here reads, in the order pydantic reads them."""
    for folder in _folders_up_to_the_repository_root(Path.cwd()):
        found = [folder / name for name in ENV_FILES if (folder / name).is_file()]
        if found:
            return found
    return []


# The walk is BOUNDED on purpose: a stray .env in a directory above the project would be the wrong
# keys, silently, which is a worse failure than finding none. docs/decisions/settings.md says why.
def _folders_up_to_the_repository_root(start: Path) -> Iterator[Path]:
    """`start`, then each parent, stopping at the first one holding a `.git` — never above it."""
    folder = start.resolve()
    for candidate in (folder, *folder.parents):
        yield candidate
        if (candidate / ".git").exists():
            return


def variable_of(field: str) -> str:
    """The environment variable one settings field reads: its own alias, or PINECALL_ + its name."""
    alias = Settings.model_fields[field].validation_alias
    return alias if isinstance(alias, str) else f"{ENV_PREFIX}{field.upper()}"

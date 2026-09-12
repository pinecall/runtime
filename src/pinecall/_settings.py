"""Every environment variable the runtime reads, declared once, for both processes."""

import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, override

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


type Role = Literal["all", "hub", "worker"]

# Who turns this box's text into vectors. TEI is a container on the box; the other two are an
# HTTP door across the internet, and the one way to retrieve on a machine TEI has no image for.
type EmbedProvider = Literal["tei", "perplexity", "openrouter"]


# A lookup never delays a reply past its budget, and a slow model at hang-up never holds the
# seal: the numbers a session waits on memory and retrieval for, then goes on without them.
# Declared here, once, because the three fields below take their defaults from it.
#
# The two lookup budgets are named for the channel because they measure two different silences. On
# a spoken call the lookups start while the caller is still talking (session/lookups.py), so what
# this number buys is the TAIL — what is left of a run when the caller stops — and it is the
# silence on the line before the agent answers. A written caller has no interim to start anything
# on, so a text turn runs the whole lookup at turn end; nobody is listening to that, so it can
# afford what a phone line cannot.
@dataclass(frozen=True)
class Budgets:
    """What each turn may wait for its lookups, and a hang-up for its memory, before going on."""

    voice_lookup_ms: int = 250
    text_lookup_ms: int = 3000
    remember_s: float = 8.0


class Settings(BaseSettings):
    """The environment, typed and frozen. One per process, built by load_settings()."""

    # A real environment variable WINS over the file: pydantic-settings reads the process
    # environment before the dotenv source. That is what a box with systemd's EnvironmentFile
    # needs, and what a laptop that exports a key from another project will feel — the export
    # shadows the file, and `env | grep PINECALL` is the first thing to run when it surprises.
    # extra="ignore" because the file may carry names this runtime does not read; an unknown key
    # is skipped, never an error at startup.
    # A box hands its secrets over as systemd credentials: one file per name under the directory
    # systemd names in CREDENTIALS_DIRECTORY, readable by this process alone and by nobody down
    # the tree (`ImportCredential=` in infra/box/*.service). pydantic reads such a directory as
    # a secrets source, matching files by the same names the environment uses, so
    # `/run/credentials/pinecall-gateway.service/DATABASE_URL` is `DATABASE_URL`. A laptop sets
    # no such variable and the source is simply absent.
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        secrets_dir=os.environ.get("CREDENTIALS_DIRECTORY"),  # noqa: TID251 — the one reader
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
    # Which of the three embeds here, and with what. The model and the door each have a default
    # per provider (providers/embed/__init__.py), so naming the provider alone is a whole
    # configuration; naming the model alone is how a Perplexity box asks for the FLAT model.
    embed_provider: EmbedProvider = Field(
        default="tei",
        validation_alias="EMBED_PROVIDER",
        description="Who embeds: tei · perplexity · openrouter. The other two need their API key.",
    )
    embed_model: str | None = Field(
        default=None,
        validation_alias="EMBED_MODEL",
        description=(
            "The embedding model. Unset: BAAI/bge-m3 · pplx-embed-context-v1-0.6b · "
            "perplexity/pplx-embed-v1-0.6b, by provider."
        ),
    )
    embed_base_url: str | None = Field(
        default=None,
        validation_alias="EMBED_BASE_URL",
        description="Where it is asked. Unset: the provider's own door, and TEI_URL for TEI.",
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
    # Not a call's vendors: the two the EMBEDDER may run on, read only by providers/embed.
    perplexity_api_key: str | None = Field(
        default=None,
        validation_alias="PERPLEXITY_API_KEY",
        description="Perplexity, an embedder: the contextual model and the flat one, direct.",
    )
    openrouter_api_key: str | None = Field(
        default=None,
        validation_alias="OPENROUTER_API_KEY",
        description="OpenRouter, the other way to the flat model. It serves no contextual door.",
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
    # How many calls this worker holds at once, MEASURED on this machine and never guessed: the
    # concurrency at which the p95 first-audio crossed 1.8 s on a ramp, one slot under it for the
    # half-second window livekit re-reads the load in (livekit/agents#4884). Set, the worker
    # reports slots to livekit — active over max — and stops taking jobs at 0.7 of them; unset,
    # it reports the machine's CPU, which is right for a box the worker shares and wrong for one
    # it has to itself. infra/box/README.md, "Slots".
    max_jobs: int | None = Field(
        default=None,
        description="Calls this worker holds at once, measured on its machine. Unset: gate on CPU.",
    )
    # livekit's worker keeps an http health server whose production default is 8081 — TEI's port
    # on this stack — and on `role=all` the two met and the worker died at bind (2026-09-11). So
    # the worker's server has a port of its own, on loopback. docs/decisions/worker.md.
    worker_http_port: int = Field(
        default=8082,
        validation_alias="PINECALL_WORKER_HTTP_PORT",
        description="Where the worker's own health server binds, on loopback. Not 8080 or 8081.",
    )
    # Its name to the hub: what `fleet list` shows and `fleet cordon` names (decisions/fleet.md).
    worker_name: str | None = Field(
        default=None,
        validation_alias="PINECALL_WORKER_NAME",
        description="What this worker is called in its heartbeats. Unset: the short hostname.",
    )
    # What the overflow agent says when every worker is full, then hangs up (worker/overflow.py).
    overflow_says: str = Field(
        default=(
            "En este momento todas nuestras líneas están ocupadas. Hemos tomado nota de su número "
            "y le devolveremos la llamada en cuanto se libere una. Gracias por su paciencia."
        ),
        validation_alias="PINECALL_OVERFLOW_SAYS",
        description="What the overflow agent says when the fleet is full, before it hangs up.",
    )
    # An app socket id as the gateway minted it, which a job puts in the `app` of its call. A
    # developer's own worker sets it so the call is served by the process they typed the command
    # in; a fleet worker on a box sets none and takes the newest holder.
    app: str | None = Field(
        default=None,
        description="The app socket a job's call claims. Unset, the call takes the newest holder.",
    )

    # ── Ours: the keys and the knobs, each under PINECALL_ ──────────────────────
    # What this box runs, as /etc/pinecall/box.env declares it and infra/box/Makefile enables
    # it: `all` on one machine, `hub` with no worker, `worker` alone dialling a hub. The doctor
    # asks a worker after no Postgres and no embedder, because a worker has neither.
    role: Role = Field(
        default="all",
        description="What this box runs: all · hub · worker. The doctor asks after what it has.",
    )
    ops_key: str | None = Field(
        default=None,
        description="The key /v1/ops/* is authenticated by. Unset, the operator API is closed.",
    )
    cloud: bool = Field(default=False, description="Pinecall's hosted gateway: a sign-up, a plan.")
    # The org's own key, as `keys issue` printed it: what the worker and the app knock with.
    api_key: str | None = Field(
        default=None,
        description="The org's own API key, as `pinecall-runtime keys issue` printed it.",
    )
    # One API key that needs no database, so a clone runs the gateway before Postgres exists —
    # and uses the database when it is there, tables and all. Set it and it is the ONLY key the
    # gateway honours, whatever the api_keys table says, which is why a box, which has tenants,
    # never sets it. Development only.
    dev_key: str | None = Field(
        default=None,
        description=(
            "One API key that needs no database, and uses one when it answers. Set it and it is "
            "the only key honoured. Dev only."
        ),
    )
    # The one secret that guards other people's secrets: a Fernet key, generated once on the box,
    # under which every tenant's own provider key is encrypted at rest — here and never in the
    # database, so a stolen dump is not a stolen tenant. Unset, every call runs on the box's keys.
    vault_key: str | None = Field(
        default=None,
        description=(
            "A Fernet key, generated once on the box by `pinecall-runtime box secrets`: a tenant's "
            "own provider keys are encrypted under it. Unset, the provider-key doors answer 503."
        ),
    )
    log_level: str = Field(
        default="INFO",
        description="How much both processes say: DEBUG, INFO, WARNING or ERROR.",
    )
    # What judging ONE call at hang-up may cost. Zero closes the door on every judge that would
    # ask a model; the policies that answer by code still answer. docs/decisions/scoring.md.
    judge_ceiling_eur: float = Field(
        default=0.002,
        description="What judging one call may spend on a model, in euros. Zero: no judge asks.",
    )

    # ── Memory and retrieval: what a turn and a hang-up wait for ──────────────
    # The language BM25 ranks in is the index's own, fixed in 0008 and 0009 (`spanish`): a
    # migration reads no setting, so there is none to read here either.
    voice_lookup_budget_ms: int = Field(
        default=Budgets.voice_lookup_ms,
        description=(
            "What a spoken turn waits for the recall and search it started while the caller was "
            "still talking, in ms. It is silence on the line, so it is small."
        ),
    )
    text_lookup_budget_ms: int = Field(
        default=Budgets.text_lookup_ms,
        description=(
            "What a written turn waits for recall and search, in ms. A written caller sends a "
            "whole message, so the lookup only starts at the end — and nobody hears the wait."
        ),
    )
    remember_budget_s: float = Field(
        default=Budgets.remember_s,
        description="What a hang-up waits for memory to be written, in s. Past it the call seals.",
    )

    @property
    def budgets(self) -> Budgets:
        """The three budgets as one thing a session is handed."""
        return Budgets(
            voice_lookup_ms=self.voice_lookup_budget_ms,
            text_lookup_ms=self.text_lookup_budget_ms,
            remember_s=self.remember_budget_s,
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

"""Every environment variable the runtime reads, declared once, for both processes."""

import os
from dataclasses import dataclass
from typing import Literal, cast, override

from pydantic import Field, model_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from pinecall._env_files import ENV_FILES, as_a_refusal, env_files_read
from pinecall._vendor_keys import VendorKeys

# Our own knobs carry this prefix; a vendor key keeps the vendor's own name (the alias on the
# field), so the SDK that reads ANTHROPIC_API_KEY by itself and this class agree.
ENV_PREFIX = "PINECALL_"


type Role = Literal["all", "hub", "worker"]

# Who turns this box's text into vectors. TEI is a container on the box; the other two are an
# HTTP door across the internet, and the one way to retrieve on a machine TEI has no image for.
type EmbedProvider = Literal["tei", "perplexity", "openrouter"]


# A lookup never delays a reply past its budget, and a slow model at hang-up never holds the
# seal: the numbers a session waits on memory and retrieval for, then goes on without them.
# Declared here, once, because the three fields below take their defaults from it. The two lookup
# budgets are named for the CHANNEL because each measures a different silence, and the field that
# reads each one says which (session/lookups.py starts a spoken call's while the caller talks).
@dataclass(frozen=True)
class Budgets:
    """What each turn may wait for its lookups, and a hang-up for its memory, before going on."""

    voice_lookup_ms: int = 250
    text_lookup_ms: int = 3000
    remember_s: float = 8.0


def _names(cls: type[BaseSettings], key: str) -> str:
    """The field an environment name belongs to: its alias, its prefixed name, or itself."""
    for name, field in cls.model_fields.items():
        alias = field.validation_alias or f"{ENV_PREFIX}{name}"
        if key.upper() in {str(alias).upper(), f"{ENV_PREFIX}{name}".upper(), name.upper()}:
            return name
    return key


class Settings(VendorKeys):
    """The environment, typed and frozen. One per process, built by load_settings()."""

    # A real environment variable WINS over the file: pydantic-settings reads the process
    # environment before the dotenv source. That is what a box with systemd's EnvironmentFile
    # needs, and what a laptop exporting a key from another project will feel — the export
    # shadows the file, and `env | grep PINECALL` is the first thing to run when it surprises.
    # extra="ignore" because the file may carry names this runtime does not read. A box hands its
    # secrets over as systemd credentials instead: one file per name under CREDENTIALS_DIRECTORY,
    # readable by this process alone (`ImportCredential=` in infra/box/*.service), which pydantic
    # reads as a secrets source — so `/run/credentials/…/DATABASE_URL` is `DATABASE_URL`.
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        secrets_dir=os.environ.get("CREDENTIALS_DIRECTORY"),  # noqa: TID251 — the one reader
        extra="ignore",
        frozen=True,
    )

    # `.env.example` writes every optional knob as a bare `NAME=`, which is how a person reads
    # "not set". For a string that is already true; for `PINECALL_MAX_JOBS=` it was not, and a
    # laptop that did nothing but `cp .env.example .env` — the first step of docs/from-zero.md —
    # could not start ANY process: `max_jobs · Input should be a valid integer`. An empty value
    # is an absent one, for every optional field, so the next `int | None` knob cannot repeat it.
    @model_validator(mode="before")
    @classmethod
    def _an_empty_value_is_no_value(cls, given: object) -> object:
        if not isinstance(given, dict):
            return given
        values = cast(dict[str, object], given)
        optional = {
            name
            for name, field in cls.model_fields.items()
            if not field.is_required() and field.default is None
        }
        return {
            key: None if value == "" and _names(cls, key) in optional else value
            for key, value in values.items()
        }

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
    # second secret to set. Without a pair this gateway verifies none and mints none.
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
    # The box's own carrier account, for the numbers it buys FOR a tenant (POST /v1/numbers/buy):
    # the same three names infra/tools/twilio_trunk.py reads. Unset, the door says so; a tenant's
    # own account is a row of the carriers table and never these.
    twilio_account_sid: str | None = Field(
        default=None,
        validation_alias="TWILIO_ACCOUNT_SID",
        description="The box's Twilio account, for the numbers it buys for a tenant.",
    )
    twilio_api_key: str | None = Field(
        default=None,
        validation_alias="TWILIO_API_KEY",
        description="An API key SID on that account, revocable on its own; else the account SID.",
    )
    twilio_api_secret: str | None = Field(
        default=None,
        validation_alias="TWILIO_API_SECRET",
        description="The API key's secret, or the account's auth token when no key is set.",
    )
    # The box's own public name — what Caddy answers to, and where a carrier sends the INVITE for
    # a number a tenant imports (sip:<domain>:5060). The box already has it in box.env.
    domain: str | None = Field(
        default=None,
        description="The box's public name: where a carrier sends a call. Unset, nothing imports.",
    )
    # The box's SECOND name, and the only thing that tells the two consoles apart: a page served
    # at this one is the sandbox's, and a request that arrives here may not run in production
    # (auth/world.py). One gateway answers both names — Caddy holds them and passes the Host
    # through — so this is a name, never a second process. Unset, the box has one console and it
    # is production's, as it was before there were two.
    sandbox_domain: str | None = Field(
        default=None,
        description="The box's second name, whose console is the sandbox's. Unset, there is one.",
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
    # hands it the entrypoint by name, so a flag parsed in the parent never reaches it — the
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
    # reports slots — active over max — and stops taking jobs at 0.7 of them; unset, it reports
    # the machine's CPU, right for a box it shares and wrong for one it has alone. "Slots".
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
    # The operator's call and not this runtime's: their box, their people. 0 is no rule at all.
    # What stops a guess is argon2id at rest and five tries a minute, never the floor.
    min_password: int = Field(
        default=8, ge=0, description="How short a member's password may be. 0 is no rule."
    )
    ops_key: str | None = Field(
        default=None,
        description="The key /v1/ops/* is authenticated by. Unset, the operator API is closed.",
    )
    cloud: bool = Field(default=False, description="Pinecall's hosted gateway: a plan, billed.")
    # Whether a stranger may make an org here. Its own flag and not `cloud`, because they are two
    # facts: a box somebody runs for their own agents wants no sign-up at all — which is why this
    # is OFF unless the person who runs the gateway turns it on — and a cloud may close sign-ups
    # without ceasing to be one. The console draws the way in only where this says so.
    signup: bool = Field(
        default=False,
        description="Whether a stranger may make an org at this gateway. Off unless you say.",
    )
    # Packages installed beside the runtime that plug a policy into its named points: how a box
    # that charges says the numbers without the runtime learning what a plan is. extensions/.
    extensions: str = Field(
        default="",
        description="Packages that plug a policy into the runtime's points, comma separated.",
    )
    # The org's own key, as `keys issue` printed it: what the WORKER knocks at its gateway with,
    # minted once by pinecall-worker-key.service and kept in the credstore. It was
    # PINECALL_API_KEY — this credential, a key source in the v2 CLI and the variable v1's SDK
    # exports, all at once, so a laptop with v1's export still live silently registered agents
    # into whatever org THAT key named. A name of its own ends the collision.
    worker_key: str | None = Field(
        default=None,
        description="The org key the worker knocks its gateway with, as `keys issue` printed it.",
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
    # The box's own mail, which is how an invitation, a password reset and a forgotten one reach
    # the person they are about instead of being copied out of an answer by hand. Generic SMTP,
    # so SES, Postmark, Mailgun or a server of one's own all fit; a credential like every other
    # secret on a box, so it is a systemd credential and never argv. An org that wired its own
    # (orgs/mail.py) is used instead; with neither, nothing is sent and every door reads as before.
    smtp_url: str | None = Field(
        default=None,
        description="smtp://user:pass@host:587, or smtps://…:465 — what this box posts mail with.",
    )
    mail_from: str | None = Field(
        default=None,
        description='Who the box\'s letters are from: "Pinecall <no-reply@example.com>".',
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

    # pydantic resolves an env_file NAME against the working directory alone, so it is the one part
    # of the config that cannot express the walk: the dotenv source is rebuilt here over the
    # absolute paths env_files_read() found, and everything else — the prefix, the encoding, its
    # place AFTER the process environment — is still model_config's. With no env_file the default
    # source is handed back untouched, which is how tests/conftest.py keeps the suite off your file.
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
    """The environment now. No hidden global; an unopenable .env is a sentence, not a trace."""
    try:
        return Settings()
    except OSError as failed:
        raise as_a_refusal(failed) from failed


def variable_of(field: str) -> str:
    """The environment variable one settings field reads: its own alias, or PINECALL_ + its name."""
    alias = Settings.model_fields[field].validation_alias
    return alias if isinstance(alias, str) else f"{ENV_PREFIX}{field.upper()}"

"""Settings: every variable the gateway and the worker read, and the three places it comes from."""

import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from pinecall.domain.errors import SettingsRefused
from pinecall.domain.names import PRODUCTION, SANDBOX, Env

# Checked in each directory up to the repository root; `runtime/.env` serves a process started
# from the checkout root. When both exist the later wins.
ENV_FILES = (".env", "runtime/.env")

# Slug-shaped: it ends up in Twilio usernames and SFU trunk names. Never empty: an empty
# agent_name makes LiveKit dispatch to every room (livekit worker.py:219).
A_FLEET_NAME = r"^[a-z0-9][a-z0-9-]{0,62}$"

UNREADABLE_ENV = "cannot read {file}: {why}. A .env that is there is never skipped in silence"

NO_STORE = (
    "PINECALL_RECORDINGS_BUCKET is a bucket of the object store, which is named by "
    "PINECALL_S3_ENDPOINT, PINECALL_S3_REGION, PINECALL_S3_ACCESS_KEY_ID and the "
    "PINECALL_S3_SECRET_ACCESS_KEY credential together; missing: {missing}"
)

# Spoken by the overflow agent when every worker is full, before hanging up.
OVERFLOW_SAYS = (
    "En este momento todas nuestras líneas están ocupadas. Hemos tomado nota de su número "
    "y le devolveremos la llamada en cuanto se libere una. Gracias por su paciencia."
)


class Settings(BaseModel):
    """Typed, frozen process settings; `load()` builds them."""

    model_config = ConfigDict(
        frozen=True, extra="ignore", validate_by_name=True, validate_by_alias=True
    )

    # ── the media plane ──
    livekit_url: str = Field(
        "ws://127.0.0.1:7880",
        alias="LIVEKIT_URL",
        description="LiveKit, which both processes talk to. One port serves ws and http.",
    )
    livekit_api_key: str | None = Field(
        None, alias="LIVEKIT_API_KEY", description="The LiveKit API key, as its own config says."
    )
    # Also signs call tokens, which are LiveKit room tokens. Without the pair, no tokens.
    livekit_api_secret: str | None = Field(
        None, alias="LIVEKIT_API_SECRET", repr=False, description="Its secret."
    )
    livekit_public_url: str | None = Field(
        None,
        alias="LIVEKIT_PUBLIC_URL",
        description="The LiveKit URL a browser is told to join. Unset, it hears LIVEKIT_URL.",
    )
    # The egress service records the room composite; only the doctor knocks here.
    egress_url: str = Field(
        "http://127.0.0.1:7980",
        alias="PINECALL_EGRESS_URL",
        description="Where the box's recorder answers its health check.",
    )
    # A box has a name per world: the request's Host says which world it is for.
    domain: str | None = Field(
        None,
        alias="PINECALL_DOMAIN",
        description="Production's name: where the public and a carrier reach the box.",
    )
    sandbox_domain: str | None = Field(
        None,
        alias="PINECALL_SANDBOX_DOMAIN",
        description="The sandbox's name, a second name of the same box. Unset, the box has one.",
    )

    # ── Postgres ──
    database_url: str = Field(
        "postgresql://pinecall:pinecall@127.0.0.1:5432/pinecall",
        alias="DATABASE_URL",
        repr=False,
        description="Postgres 17 with pgvector and pg_textsearch: the one stateful service.",
    )

    # ── the signal between gateways ──
    # A URL may carry a password. Unset, the gateway is alone on its box and keeps it in memory.
    redis_url: str | None = Field(
        None,
        alias="PINECALL_REDIS_URL",
        repr=False,
        description="The Redis the gateways tell each other what just happened on. Unset: one.",
    )

    # ── recordings ──
    recordings_root: str = Field(
        "recordings",
        alias="PINECALL_RECORDINGS",
        description="Where a kept recording lands, absolute or relative to the working directory.",
    )
    # A bucket of the object store below; never the backup's bucket, whose 35-day lifecycle rule
    # would forget a recording its org keeps longer.
    recordings_bucket: str | None = Field(
        None,
        alias="PINECALL_RECORDINGS_BUCKET",
        description="The bucket a finished recording moves to, under its org. Unset: the disk.",
    )

    # ── the object store ──
    # Any S3-compatible endpoint: AWS, Google Cloud Storage by HMAC key, R2, B2, a MinIO.
    s3_endpoint: str | None = Field(
        None,
        alias="PINECALL_S3_ENDPOINT",
        description="The S3-compatible object store what leaves the disk goes to.",
    )
    s3_region: str | None = Field(
        None,
        alias="PINECALL_S3_REGION",
        description="The region the store's signature names: the buckets' own, or `auto`.",
    )
    s3_access_key_id: str | None = Field(
        None,
        alias="PINECALL_S3_ACCESS_KEY_ID",
        description="The key the box reads and writes the store with.",
    )
    s3_secret_access_key: str | None = Field(
        None,
        alias="PINECALL_S3_SECRET_ACCESS_KEY",
        repr=False,
        description="The key's secret: a sealed credential on a box, never a file.",
    )

    # ── the worker ──
    # Read from the environment because livekit runs each job in a child process that inherits
    # only the environment. The gateway also binds this URL's host and port.
    gateway_url: str = Field(
        "http://127.0.0.1:8080",
        alias="PINECALL_GATEWAY_URL",
        description="The instance's gateway: where it binds, on loopback, and what a worker asks.",
    )
    agent: str | None = Field(
        None, alias="PINECALL_AGENT", description="The agent a job that names none is for."
    )
    max_jobs: int | None = Field(
        None,
        alias="PINECALL_MAX_JOBS",
        description="Calls this worker holds at once, measured on its machine. Unset: gate on CPU.",
    )
    worker_http_port: int = Field(
        8082,
        alias="PINECALL_WORKER_HTTP_PORT",
        description="Where the worker's own health server binds, on loopback.",
    )
    worker_name: str | None = Field(
        None,
        alias="PINECALL_WORKER_NAME",
        description="What this worker is called in its heartbeats. Unset: the short hostname.",
    )
    # Per-instance LiveKit agent name, so production and sandbox on one SFU never take each
    # other's calls. Also prefixes trunk and rule names.
    fleet: str = Field(
        "pinecall",
        alias="PINECALL_FLEET",
        pattern=A_FLEET_NAME,
        description="The fleet this worker unit joins: the name it registers under with LiveKit.",
    )
    # livekit prewarms one process per CPU, wasting RAM when two instances share a machine.
    idle_processes: int | None = Field(
        None,
        alias="PINECALL_IDLE_PROCESSES",
        description="Job processes the worker keeps warm. Unset: livekit's, one per CPU.",
    )
    overflow_says: str = Field(
        OVERFLOW_SAYS,
        alias="PINECALL_OVERFLOW_SAYS",
        description="What the overflow agent says when the fleet is full, before it hangs up.",
    )
    # A developer's worker sets this so calls reach their own app socket.
    app: str | None = Field(
        None,
        alias="PINECALL_APP",
        description="The app socket a job's call claims. Unset, the call takes the newest holder.",
    )

    # ── the box ──
    role: Literal["all", "hub", "worker"] = Field(
        "all",
        alias="PINECALL_ROLE",
        description="What this box runs: all · hub · worker. The doctor asks after what it has.",
    )
    # Brute force is limited by argon2id and rate limiting, not length.
    min_password: int = Field(
        8,
        alias="PINECALL_MIN_PASSWORD",
        ge=0,
        description="How short a member's password may be. 0 is no rule.",
    )
    ops_key: str | None = Field(
        None,
        alias="PINECALL_OPS_KEY",
        repr=False,
        description="The key /v1/ops/* is authenticated by. Unset, the operator API is closed.",
    )
    cloud: bool = Field(
        default=False, alias="PINECALL_CLOUD", description="Pinecall's hosted gateway: billed."
    )
    signup: bool = Field(
        default=False,
        alias="PINECALL_SIGNUP",
        description="Whether a stranger may make an org at this gateway. Off unless you say.",
    )
    billing_url: str | None = Field(
        None,
        alias="PINECALL_BILLING_URL",
        description="Where this box's orgs pay, https://…; unset, the box bills nobody.",
    )
    # Restricts sign-up to the bot-protected site in front of it, whose X-Pinecall-Client header
    # is then trusted as the client address.
    signup_key: str | None = Field(
        None,
        alias="PINECALL_SIGNUP_KEY",
        repr=False,
        description="The Bearer key the sign-up doors take; unset, they take anybody.",
    )
    # The mobile app's two origins are always allowed. No wildcards.
    app_origins: str = Field(
        "",
        alias="PINECALL_APP_ORIGINS",
        description="Origins besides the mobile app's two that may call /v1, comma separated.",
    )
    # Not PINECALL_API_KEY: the CLI and the SDK read that name, and a stale export once
    # registered agents into the wrong org.
    worker_key: str | None = Field(
        None,
        alias="PINECALL_WORKER_KEY",
        repr=False,
        description="The org key the worker knocks its gateway with, as `keys issue` printed it.",
    )
    # ── the runner ──
    runner_key: str | None = Field(
        None,
        alias="PINECALL_RUNNER_KEY",
        repr=False,
        description="The key a world's runner knocks with, as `keys runner` printed it.",
    )
    runner_root: str = Field(
        "/var/lib/pinecall/runner",
        alias="PINECALL_RUNNER_ROOT",
        description="Where the runner unpacks each release and installs its dependencies.",
    )
    runner_image: str = Field(
        "docker.io/library/node:24-slim",
        alias="PINECALL_RUNNER_IMAGE",
        description="The image every hosted app installs and runs in.",
    )
    runner_runtime: str = Field(
        "runsc",
        alias="PINECALL_RUNNER_RUNTIME",
        description="The OCI runtime a hosted app runs under: runsc (gVisor), or crun.",
    )
    # Kept out of the database so a stolen dump does not expose tenants' secrets. To rotate: a
    # comma-separated list, the new key first; a secret seals under the first and opens under
    # whichever sealed it.
    vault_key: str | None = Field(
        None,
        alias="PINECALL_VAULT_KEY",
        repr=False,
        description="The Fernet key every sealed secret is under; the gateway needs it.",
    )
    smtp_url: str | None = Field(
        None,
        alias="PINECALL_SMTP_URL",
        repr=False,
        description="smtp://user:pass@host:587, or smtps://…:465: what this box posts mail with.",
    )
    mail_from: str | None = Field(
        None,
        alias="PINECALL_MAIL_FROM",
        description='Who the box\'s letters are from: "Pinecall <no-reply@example.com>".',
    )
    log_level: str = Field(
        "INFO",
        alias="PINECALL_LOG_LEVEL",
        description="How much both processes say: DEBUG, INFO, WARNING or ERROR.",
    )
    # json matches the worker's own JSON logs (livekit's JsonFormatter).
    log_format: Literal["text", "json"] = Field(
        "text",
        alias="PINECALL_LOG_FORMAT",
        description="The gateway's lines: text for a terminal, json for a journal.",
    )
    otlp_endpoint: str | None = Field(
        None,
        alias="PINECALL_OTLP_ENDPOINT",
        description="Where the worker sends a call's traces, OTLP over HTTP. Unset: no trace.",
    )
    otlp_headers: str | None = Field(
        None,
        alias="PINECALL_OTLP_HEADERS",
        repr=False,
        description="Headers on every trace export, `name=value` comma-separated: a credential.",
    )
    otlp_pii: bool = Field(
        default=False,
        alias="PINECALL_OTLP_PII",
        description="Whether a trace carries what was said and what a tool got.",
    )
    # Calls are dated in the caller's zone, not the box's, so "tomorrow" means the right day.
    timezone: str = Field(
        "UTC",
        alias="PINECALL_TIMEZONE",
        description="The IANA zone a call's `today` is read in (Europe/Madrid). UTC unless set.",
    )
    # ── memory and retrieval ──
    # Unset budgets use the session's defaults.
    voice_lookup_budget_ms: int | None = Field(
        None,
        alias="PINECALL_VOICE_LOOKUP_BUDGET_MS",
        description="What a spoken turn waits for recall and search, in ms: silence on the line.",
    )
    text_lookup_budget_ms: int | None = Field(
        None,
        alias="PINECALL_TEXT_LOOKUP_BUDGET_MS",
        description="What a written turn waits for recall and search, in ms.",
    )
    remember_budget_s: float | None = Field(
        None,
        alias="PINECALL_REMEMBER_BUDGET_S",
        description="What a hang-up waits for memory to be written, in s. Past it the call seals.",
    )

    # Vendor keys keep the vendor's own variable name, so the SDK that reads ANTHROPIC_API_KEY
    # by itself and this runtime agree; the catalog asks for them by name.
    variables: Mapping[str, str] = Field(
        default_factory=dict,
        repr=False,
        description="Every variable read, for the vendor keys the catalog names.",
    )

    @field_validator("timezone")
    @classmethod
    def _a_zone_that_exists(cls, zone: str) -> str:
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"{zone!r} is not an IANA time zone (Europe/Madrid, UTC)") from None
        return zone

    # Refused where the process starts, not on the first recording a call leaves.
    @model_validator(mode="after")
    def _a_recordings_bucket_has_its_store(self) -> Self:
        named = {
            "PINECALL_S3_ENDPOINT": self.s3_endpoint,
            "PINECALL_S3_REGION": self.s3_region,
            "PINECALL_S3_ACCESS_KEY_ID": self.s3_access_key_id,
            "PINECALL_S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
        }
        missing = [name for name, value in named.items() if value is None]
        if self.recordings_bucket is not None and missing:
            raise ValueError(NO_STORE.format(missing=", ".join(missing)))
        return self

    def world_named(self, host: str | None) -> Env | None:
        """The world a request's Host names, or None where the box does not know the name."""
        name = "" if host is None else host.partition(":")[0].lower()
        if name and name == self.sandbox_domain:
            return SANDBOX
        if name and name == self.domain:
            return PRODUCTION
        return None

    # A box of one name serves both worlds at it; the console there is production's.
    def name_of(self, world: Env) -> str | None:
        """The box's name for that world, or None where it has none."""
        return (self.sandbox_domain or self.domain) if world == SANDBOX else self.domain

    def address_of(self, world: Env) -> str | None:
        """The https address of the box's name for that world, or None where it has none."""
        name = self.name_of(world)
        return None if name is None else f"https://{name}"

    def livekit_url_for(self, world: Env) -> str:
        """The LiveKit URL a browser in that world is told to join: its own name, else the box's."""
        name = self.name_of(world)
        return f"wss://{name}" if name else (self.livekit_public_url or self.livekit_url)


def load() -> Settings:
    """Build Settings from the systemd credentials, the .env files found and the environment."""
    read: dict[str, str] = {}
    credentials = os.environ.get("CREDENTIALS_DIRECTORY")
    if credentials:
        read.update(_credentials_in(Path(credentials)))
    for file in env_files():
        read.update(_env_file(file))
    read.update(os.environ)
    # A bare `NAME=` means unset, so a copied example does not hand a vendor an empty value.
    variables = {name: value for name, value in read.items() if value != ""}
    try:
        return Settings.model_validate({**variables, "variables": variables})
    except ValidationError as refused:
        raise SettingsRefused(str(refused)) from refused


def env_files() -> list[Path]:
    """Return the .env files of the first directory up to the repository root that has any."""
    for folder in _up_to_the_repository_root(Path.cwd()):
        found = [folder / name for name in ENV_FILES if (folder / name).is_file()]
        if found:
            return found
    return []


# Bounded at the repository root: a stray .env above the project would silently load wrong keys.
def _up_to_the_repository_root(start: Path) -> Iterator[Path]:
    folder = start.resolve()
    for candidate in (folder, *folder.parents):
        yield candidate
        if (candidate / ".git").exists():
            return


# On a box, secrets arrive as systemd credentials: one file per variable name.
def _credentials_in(directory: Path) -> dict[str, str]:
    return {
        file.name: file.read_text(encoding="utf-8").strip()
        for file in sorted(directory.iterdir())
        if file.is_file()
    }


# An unreadable .env fails loudly: it may hold the key that selects the right database.
def _env_file(path: Path) -> dict[str, str]:
    try:
        values = dotenv_values(path, encoding="utf-8")
    except OSError as failed:
        raise SettingsRefused(
            UNREADABLE_ENV.format(file=path, why=failed.strerror or failed)
        ) from failed
    return {name: value for name, value in values.items() if value is not None}

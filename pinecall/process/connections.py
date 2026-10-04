"""What one process opens at start and closes at the end: the database, the vault, HTTP, LiveKit."""

from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from cryptography.fernet import Fernet, MultiFernet
from livekit import api

from pinecall.domain.errors import NotAvailable, SettingsRefused
from pinecall.domain.names import PRODUCTION, SANDBOX, Env
from pinecall.postgres.pool import Pool, open_pool
from pinecall.process.settings import Settings
from pinecall.process.signal import LocalSignal, Signal, opened_signal

VARIABLE = "PINECALL_VAULT_KEY"


# The log's writer holds a connection per lane (log/_writer.py LANES), open for the process's
# life; the doors share the rest of PINECALL_DB_POOL.
WRITING = 2


NO_LIVEKIT = "LIVEKIT_API_KEY and LIVEKIT_API_SECRET are unset: this process cannot reach the SFU"


@dataclass(frozen=True)
class Connections:
    """The settings a process was given, and the five things it holds open on them."""

    settings: Settings
    # The doors'.
    pool: Pool
    # The log writer's own: never waits for a door, and a door never waits for it.
    writing: Pool
    vault: MultiFernet
    # The carriers', Meta's and the identity providers' HTTP, one pool per process.
    http: httpx.AsyncClient
    # Each world's LiveKit server API, one client per LiveKit: both worlds hold the one client
    # when the sandbox has no LiveKit of its own.
    servers: Mapping[Env, api.LiveKitAPI]
    # What the gateways tell each other; a process given no Redis keeps it to itself.
    signal: Signal = field(default_factory=LocalSignal)


UNSET = (
    f"{VARIABLE} is unset: every vendor key, mailbox and sign-in secret is sealed under it, "
    "so the gateway does not start without it. `Fernet.generate_key()` makes one"
)


NOT_A_KEY = (
    f"{VARIABLE} holds something that is not a Fernet key: a comma-separated list of "
    "32-byte url-safe base64 keys, the newest first"
)


@asynccontextmanager
async def opened(settings: Settings) -> AsyncGenerator[Connections]:
    """Open everything, the vault first: a box without its key starts nothing; closed in reverse."""
    sealed = vault_of(settings.vault_key)
    servers = servers_of(settings)
    pool = await open_pool(settings.database_url, max_size=settings.db_pool - WRITING)
    try:
        writing = await open_pool(settings.database_url, max_size=WRITING, min_size=WRITING)
    except BaseException:
        await pool.close()
        raise
    try:
        async with httpx.AsyncClient() as http, opened_signal(settings) as signal:
            yield Connections(
                settings=settings,
                pool=pool,
                writing=writing,
                vault=sealed,
                http=http,
                servers=servers,
                signal=signal,
            )
    finally:
        await closed(servers)
        await writing.close()
        await pool.close()


# livekit-api opens an HTTP session per client: one per LiveKit and process, closed with it. Both
# LiveKits take the box's one key pair, so a token the gateway signs opens a room on either.
def servers_of(settings: Settings) -> dict[Env, api.LiveKitAPI]:
    """Each world's LiveKit server API, on the box's key pair: one client where they share one."""
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise NotAvailable(NO_LIVEKIT)
    key, secret = settings.livekit_api_key, settings.livekit_api_secret
    production = api.LiveKitAPI(settings.livekit_url_of(PRODUCTION), key, secret)
    if settings.livekit_url_of(SANDBOX) == settings.livekit_url_of(PRODUCTION):
        return {PRODUCTION: production, SANDBOX: production}
    return {
        PRODUCTION: production,
        SANDBOX: api.LiveKitAPI(settings.livekit_url_of(SANDBOX), key, secret),
    }


async def closed(servers: Mapping[Env, api.LiveKitAPI]) -> None:
    """Close each LiveKit's client once, however many worlds hold it."""
    for server in dict.fromkeys(servers.values()):
        await server.aclose()


def vault_of(keys: str | None) -> MultiFernet:
    """The vault built from the setting: the first key seals, any key listed opens."""
    return MultiFernet(keyring_of(keys))


def keyring_of(keys: str | None) -> list[Fernet]:
    """Every key the setting lists, in its order: the first is the one that seals."""
    if keys is None:
        raise SettingsRefused(UNSET)
    listed = [item.strip() for item in keys.split(",") if item.strip()]
    if not listed:
        raise SettingsRefused(NOT_A_KEY)
    try:
        return [Fernet(listed_one) for listed_one in listed]
    except ValueError:
        raise SettingsRefused(NOT_A_KEY) from None

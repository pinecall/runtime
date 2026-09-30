"""What one process opens at start and closes at the end: the database, the vault, HTTP, LiveKit."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import httpx
from cryptography.fernet import Fernet, MultiFernet
from livekit import api

from pinecall.domain.errors import NotAvailable, SettingsRefused
from pinecall.postgres.pool import Pool, open_pool
from pinecall.process.settings import Settings
from pinecall.process.signal import LocalSignal, Signal, opened_signal

VARIABLE = "PINECALL_VAULT_KEY"


NO_LIVEKIT = "LIVEKIT_API_KEY and LIVEKIT_API_SECRET are unset: this process cannot reach the SFU"


@dataclass(frozen=True)
class Connections:
    """The settings a process was given, and the five things it holds open on them."""

    settings: Settings
    pool: Pool
    vault: MultiFernet
    # The carriers', Meta's and the identity providers' HTTP, one pool per process.
    http: httpx.AsyncClient
    # The SFU's server API, one per process.
    server: api.LiveKitAPI
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
    server = server_of(settings)
    pool = await open_pool(settings.database_url)
    try:
        async with httpx.AsyncClient() as http, opened_signal(settings) as signal:
            yield Connections(
                settings=settings, pool=pool, vault=sealed, http=http, server=server, signal=signal
            )
    finally:
        await server.aclose()
        await pool.close()


# livekit-api opens an HTTP session per client: one per process, closed with it.
def server_of(settings: Settings) -> api.LiveKitAPI:
    """The SFU's server API, on the box's key pair."""
    if not settings.livekit_api_key or not settings.livekit_api_secret:
        raise NotAvailable(NO_LIVEKIT)
    return api.LiveKitAPI(
        settings.livekit_url, settings.livekit_api_key, settings.livekit_api_secret
    )


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

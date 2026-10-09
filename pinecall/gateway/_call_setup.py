"""What a call of an agent is set up with: its config tuned by the scope, the keys, a refusal."""

from cryptography.fernet import MultiFernet

from pinecall.domain.agent import AgentConfig, Versions
from pinecall.domain.errors import QuotaExhausted
from pinecall.domain.scope import Scope
from pinecall.gateway import _alerts
from pinecall.log.logs import Logs
from pinecall.postgres.pool import Pool
from pinecall.process.connections import Connections
from pinecall.providers.catalog import Providers
from pinecall.providers.credentials import Keyring
from pinecall.providers.declared import apply_tuning
from pinecall.tenancy import admission, scopes, vault
from pinecall.tenancy.scopes import Picked
from pinecall.wire.events import (
    CreditsExhausted,
)


# A call's own id picks its version where the scope stands on a canary; no call is the rest.
async def tuned(
    pool: Pool,
    declared: AgentConfig,
    scope: Scope,
    configured: Providers,
    picked: Picked | None = None,
) -> tuple[AgentConfig, Versions]:
    """The declaration under the scope's settings, and the versions it was built from."""
    existing = await scopes.current(pool, scope, declared.slug, picked)
    config = apply_tuning(declared, existing.tuning, existing.lexicon, defaults=configured.defaults)
    return config, existing.versions


# Read per call, so a rotated key runs from the next call on.
async def keys_of(pool: Pool, sealed: MultiFernet, scope: Scope) -> Keyring:
    """What the org's calls in the world may run on."""
    quotas = await admission.quotas_of(pool, scope.org, scope.env)
    return Keyring(
        own=await vault.credentials_of(pool, sealed, scope.org),
        box=await vault.box_credentials(pool, sealed),
        lends=quotas.lends,
    )


# The refusal is written on the agent's log as the numbers the quota ran out at, and posted to
# the org's webhook when it has one.
async def exhausted(
    connections: Connections, logs: Logs, scope: Scope, agent: str, refused: QuotaExhausted
) -> None:
    """credits.exhausted on the agent's log, for a refusal that names its quota."""
    if refused.quota is None:
        return
    org = scope.org
    text = CreditsExhausted.model_validate(
        {"org": org, "quota": refused.quota, "used": refused.used, "limit": refused.limit}
    )
    await _alerts.raised(connections, logs, scope, agent, text)

"""Tests for what a call is set up with: the config tuned by the scope, the keys, a refusal."""

from pinecall.domain.errors import QuotaExhausted
from pinecall.domain.scope import Scope
from pinecall.gateway._call_setup import exhausted, keys_of
from pinecall.gateway._gateway import Gateway
from pinecall.log.logs import Logs
from pinecall.log.store import Store
from pinecall.process.connections import Connections
from pinecall.tenancy import vault
from pinecall.tenancy.orgs import create
from tests.conftest import postgres

AGENT = "front-desk"


@postgres
async def test_a_refusal_that_names_its_quota_is_written_on_the_agents_log(
    store: Store, connections: Connections
) -> None:
    logs = Logs(store)
    refused = QuotaExhausted("out of minutes", quota="minutes", used=30, limit=30)
    await exhausted(connections, logs, Scope("org_1", "sandbox"), AGENT, refused)
    written = await store.whole(f"@{AGENT}")
    assert [(entry.type, entry.data["quota"]) for entry in written] == [
        ("credits.exhausted", "minutes")
    ]


@postgres
async def test_a_refusal_with_no_quota_writes_nothing(
    store: Store, connections: Connections
) -> None:
    await exhausted(
        connections, Logs(store), Scope("org_1", "sandbox"), AGENT, QuotaExhausted("no")
    )
    assert await store.whole(f"@{AGENT}") == []


@postgres
async def test_a_call_runs_on_the_orgs_own_keys_and_the_boxs_it_lends(wired: Gateway) -> None:
    pool, sealed = wired.connections.pool, wired.connections.vault
    org = await create(pool, "clinica-norte", "Clínica Norte")
    await vault.put_credentials(pool, sealed, org.id, "acme", {"api_key": "the org's"})
    keyring = await keys_of(pool, sealed, Scope(org.id, "sandbox"))
    assert keyring.own["acme"] == {"api_key": "the org's"}
    assert "acme" in keyring.box

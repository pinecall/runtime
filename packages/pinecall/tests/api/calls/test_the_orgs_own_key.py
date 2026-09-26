"""Whose LLM key a written call runs on: the org's own when it brought one, the box's when not."""

import pytest
from starlette.testclient import TestClient

from pinecall.orgs.vault import Vault
from pinecall.types import ProviderKeys
from tests.api.conftest import AN_ORG
from tests.api.talking import a_call_the_app_ends, an_app, declared

pytestmark = pytest.mark.unit

# The clinic's own account with the vendor, as one row of the vault holds it.
THE_CLINICS_OWN = "sk-the-clinics-own-anthropic-account"

# What that row is replaced with when the tenant rotates it, which is an upsert and nothing else.
ROTATED = "sk-the-clinics-second-anthropic-account"


async def test_a_chat_call_of_an_org_that_brought_a_key_is_built_with_that_key(
    gateway: TestClient, vault: Vault | None, keys_asked: list[ProviderKeys]
) -> None:
    """Criterion 1: a written call reaches the vendor on the tenant's account, as a voice one."""
    assert vault is not None
    await vault.put(AN_ORG.id, "anthropic", THE_CLINICS_OWN)
    with an_app(gateway) as app_socket:
        declared(app_socket)
        a_call_the_app_ends(gateway, app_socket)
    assert keys_asked[-1] == {"anthropic": THE_CLINICS_OWN}


async def test_an_org_that_brought_none_runs_on_the_boxs_own_keys(
    gateway: TestClient, keys_asked: list[ProviderKeys]
) -> None:
    """Managed is the absence of a row: the empty set, and the vendor files read the environment."""
    with an_app(gateway) as app_socket:
        declared(app_socket)
        a_call_the_app_ends(gateway, app_socket)
    assert keys_asked[-1] == {}


# The vault is asked when the socket opens and never before, so there is no table of orgs to go
# stale: a key set a moment ago is the key the very next call runs on.
async def test_a_rotated_key_is_the_one_the_next_call_runs_on(
    gateway: TestClient, vault: Vault | None, keys_asked: list[ProviderKeys]
) -> None:
    """No process-wide cache outlives the row: two calls, two reads, the second one the new key."""
    assert vault is not None
    await vault.put(AN_ORG.id, "anthropic", THE_CLINICS_OWN)
    with an_app(gateway) as app_socket:
        declared(app_socket)
        a_call_the_app_ends(gateway, app_socket)
        await vault.put(AN_ORG.id, "anthropic", ROTATED)
        a_call_the_app_ends(gateway, app_socket)
    assert [keys["anthropic"] for keys in keys_asked] == [THE_CLINICS_OWN, ROTATED]

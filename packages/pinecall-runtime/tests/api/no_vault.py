"""A runtime given no PINECALL_VAULT_KEY: what it runs on, and the one answer its doors give."""

import httpx
import pytest

from pinecall.orgs.box_settings import BoxSettings
from pinecall.orgs.box_settings_memory import MemoryBoxSettings
from pinecall.orgs.vault import NO_VAULT_KEY, Vault
from pinecall.settings import Settings
from tests.api.conftest import AN_OPS_KEY


class WithNoVaultKey:
    """A tenant's sealed doors on a box with no vault: every one says the vault's own sentence."""

    door: str

    @pytest.fixture
    def settings(self) -> Settings:
        return Settings(world="production", ops_key=AN_OPS_KEY)

    @pytest.fixture
    def vault(self) -> Vault | None:
        """Nothing is sealed on such a box: the provider keys go the same way (orgs/vault.py)."""
        return None

    async def test_the_doors_answer_503_with_the_vaults_own_sentence(
        self, tenant_http: httpx.AsyncClient
    ) -> None:
        answer = await tenant_http.get(self.door)
        assert answer.status_code == 503 and answer.json()["detail"] == NO_VAULT_KEY


class OnABoxWithNoVaultKey:
    """The box's own settings with no vault key in them: an operator's secret cannot be kept."""

    @pytest.fixture
    def box_settings(self) -> BoxSettings:
        return MemoryBoxSettings(None)

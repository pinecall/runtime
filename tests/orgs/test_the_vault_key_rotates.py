"""PINECALL_VAULT_KEY as a list: sealed under the first, opened under whichever key sealed it."""

import pytest
from cryptography.fernet import Fernet

from pinecall.orgs.vault import NoVaultKey, a_cipher

pytestmark = pytest.mark.unit

OLD = Fernet.generate_key().decode()
NEW = Fernet.generate_key().decode()


def test_a_secret_sealed_under_the_old_key_opens_once_the_new_one_is_in_front() -> None:
    sealed_before = a_cipher(OLD).encrypt(b"sk-tenant").decode()
    rotated = a_cipher(f"{NEW}, {OLD}")
    assert rotated.decrypt(sealed_before.encode()) == b"sk-tenant"
    # And what is sealed now is sealed under the new key alone: the old one can go afterwards.
    sealed_now = rotated.encrypt(b"sk-tenant")
    assert a_cipher(NEW).decrypt(sealed_now) == b"sk-tenant"


def test_a_list_with_a_key_that_is_not_one_is_refused_naming_the_variable() -> None:
    with pytest.raises(NoVaultKey, match="PINECALL_VAULT_KEY is not a Fernet key"):
        a_cipher(f"{NEW},not-a-key")
    with pytest.raises(NoVaultKey):
        a_cipher("")

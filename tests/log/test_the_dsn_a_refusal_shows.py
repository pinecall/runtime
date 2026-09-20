"""A refusal names the database it could not open — with the password out of it."""

import pytest

from pinecall.log.store.postgres import without_password

pytestmark = pytest.mark.unit


def test_the_password_never_travels_with_the_url() -> None:
    shown = without_password("postgresql://pinecall:s3cret@127.0.0.1:5432/pinecall")
    assert shown == "postgresql://pinecall@127.0.0.1:5432/pinecall"
    assert "s3cret" not in shown


def test_a_url_with_no_password_is_itself() -> None:
    assert without_password("postgresql://127.0.0.1:5432/pinecall") == "postgresql://127.0.0.1:5432/pinecall"


def test_an_ipv6_host_keeps_the_brackets_that_make_it_an_address() -> None:
    """`::1:5432` is not an address, and a laptop's second Postgres is reached at `[::1]`."""
    assert without_password("postgresql://pinecall:p@[::1]:5432/pinecall") == "postgresql://pinecall@[::1]:5432/pinecall"

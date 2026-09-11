"""Which key a worker sends its gateway, and the one case where the exported one is wrong."""

import pytest

from pinecall._settings import Settings
from pinecall.worker.main import the_key_for

pytestmark = pytest.mark.unit

A_DEV_KEY = "pc_dev_the_one_a_local_gateway_honours"
AN_ORG_KEY = "pc_the_row_keys_issue_printed"
LOCAL = "http://127.0.0.1:8080"
A_BOX = "https://gateway.example.com"


def _settings(url: str = LOCAL, api_key: str | None = None, dev_key: str | None = None) -> Settings:
    """A settings object carrying only what this rule reads."""
    return Settings.model_construct(gateway_url=url, api_key=api_key, dev_key=dev_key)


def test_a_box_knocks_with_the_key_that_was_issued_to_it() -> None:
    """The ordinary case: a real gateway, a real row in api_keys, no dev key anywhere."""
    assert the_key_for(_settings(A_BOX, api_key=AN_ORG_KEY)) == AN_ORG_KEY


def test_a_laptop_with_only_a_dev_key_knocks_with_it() -> None:
    assert the_key_for(_settings(dev_key=A_DEV_KEY)) == A_DEV_KEY


# The bug this test exists for: PINECALL_API_KEY exported in the shell, a gateway running on a dev
# key, and every job of a spoken suite dying on `GET /v1/routes: 401` until the run timed out.
def test_a_local_gateway_takes_the_dev_key_even_when_an_org_key_is_exported() -> None:
    """A dev-key gateway opens no database, so the org key it is being handed cannot exist there."""
    assert the_key_for(_settings(api_key=AN_ORG_KEY, dev_key=A_DEV_KEY)) == A_DEV_KEY


def test_a_remote_gateway_still_prefers_the_org_key_over_a_leftover_dev_key() -> None:
    """A box runs on issued keys and never on a dev key: a dev key in the .env is not about it."""
    both = _settings(A_BOX, api_key=AN_ORG_KEY, dev_key=A_DEV_KEY)
    assert the_key_for(both) == AN_ORG_KEY


def test_a_worker_with_neither_sends_no_header_rather_than_the_word_none() -> None:
    assert the_key_for(_settings()) == ""

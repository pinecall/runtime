"""Which key a worker sends its gateway, and the one case where the exported one is wrong."""

import pytest

from pinecall._settings import Settings
from pinecall.auth.dev_file import Door
from pinecall.worker.main import the_key_for

pytestmark = pytest.mark.unit

A_DEV_KEY = "pc_dev_the_one_a_local_gateway_honours"
AN_ORG_KEY = "pc_the_row_keys_issue_printed"
LOCAL = "http://127.0.0.1:8080"
A_BOX = "https://gateway.example.com"

THE_LOCAL_DOOR = Door(url=LOCAL, key=A_DEV_KEY)
NO_DOOR = None


def _settings(url: str = LOCAL, api_key: str | None = None, dev_key: str | None = None) -> Settings:
    """A settings object carrying only what this rule reads."""
    return Settings.model_construct(gateway_url=url, api_key=api_key, dev_key=dev_key)


def test_a_box_knocks_with_the_key_that_was_issued_to_it() -> None:
    """The ordinary case: a real gateway, a real row in api_keys, no dev key anywhere."""
    assert the_key_for(_settings(A_BOX, api_key=AN_ORG_KEY), NO_DOOR) == AN_ORG_KEY


def test_a_worker_knocking_at_the_door_a_local_gateway_left_takes_its_key() -> None:
    """The file is the gateway's own word about which key it honours; nothing has to be exported."""
    assert the_key_for(_settings(), THE_LOCAL_DOOR) == A_DEV_KEY


# The bug this test exists for: PINECALL_API_KEY exported in the shell, a gateway running on a dev
# key, and every job of a spoken suite dying on `GET /v1/routes: 401` until the run timed out.
def test_that_door_takes_the_dev_key_even_when_an_org_key_is_exported() -> None:
    """A dev-key gateway opens no database, so the org key it is being handed cannot exist there."""
    assert the_key_for(_settings(api_key=AN_ORG_KEY), THE_LOCAL_DOOR) == A_DEV_KEY


def test_a_door_left_by_another_gateway_changes_nothing_for_a_box() -> None:
    """A laptop that also runs a box's worker: the file is about 127.0.0.1, not about the box."""
    assert the_key_for(_settings(A_BOX, api_key=AN_ORG_KEY), THE_LOCAL_DOOR) == AN_ORG_KEY


def test_a_local_gateway_with_a_database_and_no_door_takes_the_org_key() -> None:
    """Loopback is not the rule: a gateway on issued keys writes no file; its keys are its own."""
    assert the_key_for(_settings(api_key=AN_ORG_KEY), NO_DOOR) == AN_ORG_KEY


def test_a_dev_key_exported_by_hand_still_opens_a_gateway_that_left_no_file() -> None:
    """The order the tenant's CLI keeps too: nothing that worked before the file existed stops."""
    assert the_key_for(_settings(dev_key=A_DEV_KEY), NO_DOOR) == A_DEV_KEY


def test_a_worker_with_neither_sends_no_header_rather_than_the_word_none() -> None:
    assert the_key_for(_settings(), NO_DOOR) == ""

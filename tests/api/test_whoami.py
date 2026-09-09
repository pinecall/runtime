"""GET /v1/whoami: the org, the key's id and its label — and never the key or its hash."""

import pytest
from starlette.testclient import TestClient

from tests.api.conftest import A_KEY
from tests.api.talking import got

pytestmark = pytest.mark.unit

WHOAMI = "/v1/whoami"


def test_the_door_names_the_org_the_key_belongs_to(gateway: TestClient) -> None:
    status, body = got(gateway, WHOAMI)

    assert status == 200
    assert body == {"org": "clinica", "key_id": "k_1", "label": "ring 0"}


def test_the_answer_carries_neither_the_key_nor_its_hash(gateway: TestClient) -> None:
    _, body = got(gateway, WHOAMI)

    assert A_KEY not in str(body)
    assert not any("hash" in field or "fingerprint" in field for field in body)


def test_a_terminal_with_no_key_is_told_which_door_this_is(gateway: TestClient) -> None:
    status, body = got(gateway, WHOAMI, bearer=None)

    assert status == 401
    assert body["detail"] == "this door takes an API key"


def test_a_key_this_gateway_never_issued_is_told_nothing_about_why(gateway: TestClient) -> None:
    status, _ = got(gateway, WHOAMI, bearer="pk_a_key_nobody_ever_issued")

    assert status == 401

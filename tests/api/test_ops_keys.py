"""An org's keys against the real app: issued once, listed by hash, revoked and never deleted."""

import httpx
import pytest

from pinecall.auth.keys import MemoryKeys, fingerprint
from tests.api.conftest import AN_OPS_KEY, over_the_asgi_app

pytestmark = pytest.mark.unit

OPS_KEYS = "/v1/ops/keys"
ROUTES = "/v1/routes"
ORG = "clinica"
ORGS_KEYS = f"/v1/ops/orgs/{ORG}/keys"


async def issue(ops_http: httpx.AsyncClient, label: str | None = None) -> dict[str, object]:
    """One key for the org, as `keys issue` asks for it."""
    answer = await ops_http.post(ORGS_KEYS, json={"label": label})
    assert answer.status_code == 200, answer.text
    return answer.json()


async def test_issuing_answers_with_the_key_and_the_record_it_was_written_under(
    ops_http: httpx.AsyncClient, keys: MemoryKeys
) -> None:
    """The one door in the runtime that carries a key in the clear, and it carries it once."""
    issued = await issue(ops_http, label="the worker on this box")
    assert str(issued["key"]).startswith("pk_")
    assert (issued["org"], issued["label"]) == (ORG, "the worker on this box")
    assert await keys.verify(str(issued["key"])) is not None


async def test_the_listing_shows_the_fingerprint_and_never_the_key(
    ops_http: httpx.AsyncClient,
) -> None:
    """A key is printed at issue and nowhere else: no verb, ever, reads one back."""
    issued = await issue(ops_http)
    listing = await ops_http.get(ORGS_KEYS)
    rows = listing.json()
    assert listing.status_code == 200
    assert fingerprint(str(issued["key"])) in [row["fingerprint"] for row in rows]
    assert str(issued["key"]) not in listing.text


async def test_revoking_keeps_the_row_and_closes_the_key(
    ops_http: httpx.AsyncClient, keys: MemoryKeys
) -> None:
    """The row grows a revoked_at so the log entries that name the key stay readable."""
    issued = await issue(ops_http)
    hashed = fingerprint(str(issued["key"]))
    revoked = await ops_http.post(f"{OPS_KEYS}/{hashed}/revoke")
    assert revoked.status_code == 200
    assert await keys.verify(str(issued["key"])) is None
    rows = (await ops_http.get(ORGS_KEYS)).json()
    still_there = [row for row in rows if row["fingerprint"] == hashed]
    assert len(still_there) == 1
    assert still_there[0]["revoked_at"] is not None


async def test_revoking_a_fingerprint_nobody_answers_to_is_a_refusal(
    ops_http: httpx.AsyncClient,
) -> None:
    """`keys revoke` on a typo must never read as done."""
    refused = await ops_http.post(f"{OPS_KEYS}/{'a' * 64}/revoke")
    assert refused.status_code == 404


async def test_the_ops_doors_take_the_ops_key_and_nothing_else(
    ops_http: httpx.AsyncClient,
) -> None:
    """An API key does not open an operator's door, however real it is."""
    issued = await issue(ops_http)
    async with over_the_asgi_app(f"Bearer {issued['key']}") as knocking:
        assert (await knocking.get(ORGS_KEYS)).status_code == 401


# The exact check tk-8712ac ran against a gateway in the box's posture, and the reason this card
# exists: before it, nothing could issue the key that opens this door, so the box admitted nobody.
async def test_a_key_issued_here_opens_the_fleets_own_door_and_no_key_opens_nothing(
    ops_http: httpx.AsyncClient,
) -> None:
    """GET /v1/routes is 200 with the issued key and 401 without it."""
    issued = await issue(ops_http)
    async with over_the_asgi_app(f"Bearer {issued['key']}") as worker:
        assert (await worker.get(ROUTES)).status_code == 200
    async with over_the_asgi_app("") as stranger:
        assert (await stranger.get(ROUTES)).status_code == 401
    async with over_the_asgi_app(f"Bearer {AN_OPS_KEY}") as operator:
        assert (await operator.get(ROUTES)).status_code == 401, "the ops key is not a org's key"

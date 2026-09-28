"""Tests for the keys doors: the listing by fingerprint, a server's token, a revoke."""

from pinecall.domain.person import SERVER_SCOPES, Member, Role
from pinecall.tenancy import keys, people
from tests.conftest import Knocking, postgres

KEYS = "/v1/keys"


async def a_person(knocking: Knocking, email: str, role: Role) -> tuple[Member, str]:
    """An active member and a key of theirs."""
    pool = knocking.gateway.connections.pool
    invited = await people.invite(
        pool,
        knocking.org.id,
        people.Invitee(email, email.split("@", maxsplit=1)[0], role),
        seats=None,
    )
    member = await people.update(
        pool, knocking.org.id, invited.member.id, people.Change(status="active")
    )
    _, secret = await keys.person_key(pool, member, label="laptop")
    return member, secret


@postgres
async def test_a_person_makes_a_servers_token_answered_once_in_the_world_asked(
    knocking: Knocking,
) -> None:
    ana, anas = await a_person(knocking, "ana@clinica.test", "admin")
    async with knocking.http(anas) as console:
        made = await console.post(KEYS, json={"label": "clinica web", "env": "production"})
        sandbox = await console.post(KEYS, json={"label": "staging", "env": "sandbox"})
        listing = await console.get(KEYS)
    body = made.json()
    assert made.status_code == 200
    assert body["key"].startswith("pc_live_")
    assert sandbox.json()["key"].startswith("pc_test_")
    assert (body["label"], body["env"], body["subject"]) == ("clinica web", "production", None)
    assert body["scopes"] == sorted(SERVER_SCOPES)
    assert body["key"] not in listing.text
    row = next(row for row in listing.json() if row["label"] == "clinica web")
    assert (row["kind"], row["env"], row["created_by"]) == ("server", "production", ana.name)


@postgres
async def test_a_production_token_takes_production_access_and_a_servers_key_makes_none(
    knocking: Knocking,
) -> None:
    _, bos = await a_person(knocking, "bo@clinica.test", "developer")
    async with knocking.http(bos) as console:
        production = await console.post(KEYS, json={"label": "x", "env": "production"})
        sandbox = await console.post(KEYS, json={"label": "x", "env": "sandbox"})
        nowhere = await console.post(KEYS, json={"label": "x", "env": "staging"})
    async with knocking.http(knocking.app["production"]) as server:
        by_a_server = await server.post(KEYS, json={"label": "x", "env": "production"})
    assert production.status_code == 403
    assert "production access" in production.json()["detail"]
    assert sandbox.status_code == 200
    assert nowhere.status_code == 400
    assert by_a_server.status_code == 403
    assert "made by a person" in by_a_server.json()["detail"]


@postgres
async def test_the_listing_shows_servers_and_your_own_and_every_persons_with_keys(
    knocking: Knocking,
) -> None:
    ana, anas = await a_person(knocking, "ana@clinica.test", "admin")
    bo, bos = await a_person(knocking, "bo@clinica.test", "developer")
    async with knocking.http(bos) as developer:
        theirs = (await developer.get(KEYS)).json()
    async with knocking.http(anas) as admin:
        every = (await admin.get(KEYS)).json()
    subjects = {row["name"] for row in theirs if row["kind"] == "person"}
    assert subjects == {bo.name}, "a developer sees their own person keys only"
    assert {row["name"] for row in every if row["kind"] == "person"} == {ana.name, bo.name}
    assert all(row["env"] is None for row in every if row["kind"] == "person")
    assert anas not in str(every)
    assert any(row["kind"] == "server" for row in theirs)


@postgres
async def test_revoking_keeps_the_row_and_closes_the_key(knocking: Knocking) -> None:
    pool = knocking.gateway.connections.pool
    _, anas = await a_person(knocking, "ana@clinica.test", "admin")
    async with knocking.http(anas) as console:
        made = (await console.post(KEYS, json={"label": "web", "env": "sandbox"})).json()
        row = next(row for row in (await console.get(KEYS)).json() if row["label"] == "web")
        revoked = await console.post(f"{KEYS}/{row['fingerprint']}/revoke")
        again = await console.post(f"{KEYS}/{row['fingerprint']}/revoke")
        after = next(row for row in (await console.get(KEYS)).json() if row["label"] == "web")
    assert revoked.json() == {"fingerprint": row["fingerprint"], "revoked": True}
    assert again.status_code == 404, "a revoked key reads like nobody's"
    assert after["revoked_at"] is not None
    assert await keys.verify(pool, made["key"]) is None


@postgres
async def test_a_person_stops_their_own_and_one_they_made_and_nothing_else(
    knocking: Knocking,
) -> None:
    _, anas = await a_person(knocking, "ana@clinica.test", "admin")
    _, bos = await a_person(knocking, "bo@clinica.test", "developer")
    async with knocking.http(anas) as admin:
        await admin.post(KEYS, json={"label": "anas", "env": "sandbox"})
        rows = (await admin.get(KEYS)).json()
    fingerprint = next(row["fingerprint"] for row in rows if row["label"] == "anas")
    async with knocking.http(bos) as developer:
        refused = await developer.post(f"{KEYS}/{fingerprint}/revoke")
        own = next(row for row in (await developer.get(KEYS)).json() if row["kind"] == "person")
        stopped = await developer.post(f"{KEYS}/{own['fingerprint']}/revoke")
    async with knocking.http(knocking.app["production"]) as server:
        nobodys = await server.post(f"{KEYS}/{'a' * 64}/revoke")
    assert refused.status_code == 404
    assert stopped.status_code == 200
    assert nobodys.status_code == 404

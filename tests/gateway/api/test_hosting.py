"""Tests for the hosting doors: a release uploaded, read back, dropped, and the org's secrets."""

import httpx
import pytest

from pinecall.domain.org import Quotas
from pinecall.tenancy import admission, keys
from tests.conftest import Knocking, issued, postgres
from tests.tenancy.test_hosting import PROJECT, tarball

RELEASES = "/v1/hosted/support/releases"


async def uploaded(knocking: Knocking, path: str = RELEASES, note: str = "") -> httpx.Response:
    async with knocking.http(knocking.app["production"]) as http:
        return await http.post(path, content=PROJECT, params={"note": note})


@postgres
async def test_an_org_the_box_hosts_nothing_for_lists_no_apps(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["production"]) as http:
        listed = await http.get("/v1/hosted")
    assert (listed.status_code, listed.json()) == (200, {"apps": []})


@postgres
async def test_the_first_upload_makes_the_app_and_is_release_one(knocking: Knocking) -> None:
    first = await uploaded(knocking, note="  the first  ")
    assert first.status_code == 200, first.text
    row = first.json()
    assert (row["name"], row["release"], row["bytes"]) == ("support", 1, len(PROJECT))
    assert (row["note"], len(row["sha256"])) == ("the first", 64)
    assert row["author"] != ""
    async with knocking.http(knocking.app["production"]) as http:
        listed = await http.get("/v1/hosted")
    [app] = listed.json()["apps"]
    assert (app["name"], app["release"]) == ("support", 1)


@postgres
async def test_a_second_upload_is_release_two_and_the_list_is_newest_first(
    knocking: Knocking,
) -> None:
    await uploaded(knocking)
    second = await uploaded(knocking)
    async with knocking.http(knocking.app["production"]) as http:
        listed = await http.get(RELEASES)
    assert second.json()["release"] == 2
    assert [row["release"] for row in listed.json()["releases"]] == [2, 1]


@postgres
async def test_a_release_comes_back_as_the_tarball_it_was_uploaded_as(knocking: Knocking) -> None:
    await uploaded(knocking)
    async with knocking.http(knocking.app["production"]) as http:
        source = await http.get(f"{RELEASES}/1/source")
        nobody = await http.get(f"{RELEASES}/2/source")
    assert (source.status_code, source.headers["content-type"]) == (200, "application/gzip")
    assert source.content == PROJECT
    assert nobody.status_code == 404


@postgres
async def test_what_production_hosts_the_sandbox_does_not(knocking: Knocking) -> None:
    await uploaded(knocking)
    async with knocking.http(knocking.app["sandbox"]) as http:
        listed = await http.get("/v1/hosted")
        releases = await http.get(RELEASES)
    assert listed.json() == {"apps": []}
    assert releases.status_code == 404


@postgres
async def test_the_app_is_given_a_server_token_and_dropping_it_revokes_the_token(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    await uploaded(knocking)
    [minted] = [
        row for row in await keys.listed(pool, knocking.org.id) if row.key.label is not None
    ]
    assert (minted.key.label, minted.revoked_at) == ("hosted app support", None)
    async with knocking.http(knocking.app["production"]) as http:
        dropped = await http.delete("/v1/hosted/support")
        again = await http.delete("/v1/hosted/support")
        listed = await http.get("/v1/hosted")
    assert (dropped.status_code, again.status_code) == (204, 404)
    assert listed.json() == {"apps": []}
    [revoked] = [
        row for row in await keys.listed(pool, knocking.org.id) if row.key.label is not None
    ]
    assert revoked.revoked_at is not None


@postgres
async def test_an_org_at_its_quota_is_refused_a_new_app_and_not_a_new_release(
    knocking: Knocking,
) -> None:
    pool = knocking.gateway.connections.pool
    await admission.set_quotas(pool, knocking.org.id, "production", Quotas(hosted_apps=1))
    await uploaded(knocking)
    another = await uploaded(knocking, "/v1/hosted/billing/releases")
    again = await uploaded(knocking)
    assert another.status_code == 429
    assert "1 of its 1 hosted apps in the production" in another.json()["detail"]
    assert again.json()["release"] == 2


@postgres
async def test_an_org_whose_quota_is_zero_hosts_nothing(knocking: Knocking) -> None:
    pool = knocking.gateway.connections.pool
    await admission.set_quotas(pool, knocking.org.id, "production", Quotas(hosted_apps=0))
    refused = await uploaded(knocking)
    assert refused.status_code == 429


@postgres
@pytest.mark.parametrize("name", ["Support", "support_line", "-support"])
async def test_a_name_that_is_not_a_slug_is_refused(knocking: Knocking, name: str) -> None:
    refused = await uploaded(knocking, f"/v1/hosted/{name}/releases")
    assert refused.status_code == 400
    assert "lowercase words joined by dashes" in refused.json()["detail"]


@postgres
async def test_an_upload_that_is_not_a_safe_tarball_is_refused_and_makes_no_app(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as http:
        garbage = await http.post(RELEASES, content=b"not a tarball")
        escaping = await http.post(RELEASES, content=tarball({"../outside": b"x"}))
        listed = await http.get("/v1/hosted")
    assert (garbage.status_code, escaping.status_code) == (400, 400)
    assert "leaves the project" in escaping.json()["detail"]
    assert listed.json() == {"apps": []}


@postgres
async def test_a_key_that_holds_no_agents_opens_none_of_these_doors(knocking: Knocking) -> None:
    pool = knocking.gateway.connections.pool
    reader = await issued(pool, knocking.org.id, "production", frozenset({"calls"}))
    async with knocking.http(reader) as http:
        answers = [
            await http.get("/v1/hosted"),
            await http.post(RELEASES, content=PROJECT),
            await http.get("/v1/secrets"),
            await http.put("/v1/secrets/CRM_TOKEN", json={"value": "x"}),
        ]
    assert [answer.status_code for answer in answers] == [403, 403, 403, 403]


@postgres
async def test_a_secret_set_is_listed_by_name_and_no_door_answers_its_value(
    knocking: Knocking,
) -> None:
    async with knocking.http(knocking.app["production"]) as http:
        written = await http.put("/v1/secrets/CRM_TOKEN", json={"value": "made-up-by-this-test"})
        listed = await http.get("/v1/secrets")
    async with knocking.http(knocking.app["sandbox"]) as http:
        elsewhere = await http.get("/v1/secrets")
    assert written.status_code == 200, written.text
    [row] = listed.json()["secrets"]
    assert row["name"] == "CRM_TOKEN"
    assert set(row) == {"name", "set_by", "set_at"}
    assert "made-up-by-this-test" not in written.text + listed.text
    assert elsewhere.json() == {"secrets": []}


@postgres
async def test_a_secrets_name_is_a_shells_and_never_the_boxs(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["production"]) as http:
        lower = await http.put("/v1/secrets/crm_token", json={"value": "x"})
        reserved = await http.put("/v1/secrets/PINECALL_KEY", json={"value": "x"})
    assert (lower.status_code, reserved.status_code) == (400, 400)
    assert "the box's to set" in reserved.json()["detail"]


@postgres
async def test_a_secret_dropped_is_gone_and_a_name_nobody_set_is_a_404(knocking: Knocking) -> None:
    async with knocking.http(knocking.app["production"]) as http:
        await http.put("/v1/secrets/CRM_TOKEN", json={"value": "x"})
        dropped = await http.delete("/v1/secrets/CRM_TOKEN")
        again = await http.delete("/v1/secrets/CRM_TOKEN")
    assert (dropped.status_code, dropped.json()) == (200, {"secrets": []})
    assert again.status_code == 404

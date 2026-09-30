"""Tests for what the box hosts for an org: an app, its token, and its releases."""

import gzip
import io
import tarfile

import pytest
from cryptography.fernet import Fernet

from pinecall.domain.errors import DeclarationRefused, NotFound
from pinecall.postgres.pool import Pool
from pinecall.process.connections import vault_of
from pinecall.tenancy import keys
from pinecall.tenancy.hosting import (
    LARGEST_SOURCE,
    HostedApp,
    apps_of,
    checked_name,
    checked_source,
    drop_app,
    failed,
    host_of,
    hosted_in,
    is_a_host_of,
    is_hosted,
    keep_release,
    key_of,
    open_app,
    release_of,
    releases_of,
    source_of,
    went_live,
)
from pinecall.tenancy.org_secrets import Secret, put_secret
from pinecall.tenancy.orgs import remove
from pinecall.tenancy.vault import opened
from tests.conftest import postgres
from tests.tenancy.conftest import an_org

VAULT = vault_of(Fernet.generate_key().decode())


def tarball(files: dict[str, bytes]) -> bytes:
    """A gzipped tarball of these files."""
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as tar:
        for path, content in files.items():
            member = tarfile.TarInfo(path)
            member.size = len(content)
            tar.addfile(member, io.BytesIO(content))
    return packed.getvalue()


def tarball_of(member: tarfile.TarInfo) -> bytes:
    """A gzipped tarball holding this one member, whatever it is."""
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as tar:
        tar.addfile(member)
    return packed.getvalue()


PROJECT = tarball({"package.json": b"{}", "agents/support/agent.ts": b"export default 1"})


def test_a_tarball_of_files_is_a_release_with_its_sha256() -> None:
    source = checked_source(PROJECT)
    assert source.data == PROJECT
    assert len(source.sha256) == 64


@pytest.mark.parametrize(
    "upload",
    [b"", b"not a tarball", gzip.compress(b"gzip, no tar inside", mtime=0)],
    ids=["nothing", "words", "gzip-of-words"],
)
def test_anything_but_a_gzipped_tarball_is_refused(upload: bytes) -> None:
    with pytest.raises(DeclarationRefused, match="gzipped tarball"):
        checked_source(upload)


@pytest.mark.parametrize("path", ["../outside.txt", "agents/../../outside.txt", "/etc/passwd"])
def test_a_path_that_leaves_the_project_is_refused(path: str) -> None:
    with pytest.raises(DeclarationRefused, match="leaves the project"):
        checked_source(tarball({path: b"x"}))


def test_a_link_is_refused() -> None:
    link = tarfile.TarInfo("agents/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    with pytest.raises(DeclarationRefused, match="a link or a device"):
        checked_source(tarball_of(link))


def test_an_upload_over_the_ceiling_is_refused_before_it_is_read() -> None:
    with pytest.raises(DeclarationRefused, match="10 MB at most"):
        checked_source(b"\0" * (LARGEST_SOURCE + 1))


def test_a_small_upload_that_unpacks_past_the_ceiling_is_refused() -> None:
    bomb = tarfile.TarInfo("zeros")
    bomb.size = 101 * 1024 * 1024
    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:gz") as tar:
        tar.addfile(bomb, io.BytesIO(b"\0" * bomb.size))
    with pytest.raises(DeclarationRefused, match="unpacks to 100 MB"):
        checked_source(packed.getvalue())


@postgres
async def test_an_app_opened_is_hosted_and_holds_a_live_server_token_sealed(pool: Pool) -> None:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="production", name="support")
    assert not await is_hosted(pool, app)
    await open_app(pool, VAULT, app, created_by="m_ana")
    assert await is_hosted(pool, app)
    [listed] = await apps_of(pool, org.id, "production")
    assert (listed.name, listed.release, listed.created_by) == ("support", None, "m_ana")
    [minted] = await keys.listed(pool, org.id)
    assert (minted.key.label, minted.key.env) == ("hosted app support", "production")
    assert minted.revoked_at is None
    async with pool.connection() as connection:
        found = await connection.execute("SELECT sealed_key, key_fingerprint FROM hosted_apps")
        row = await found.fetchone()
    assert row is not None
    secret = opened(VAULT, row["sealed_key"])
    assert isinstance(secret, str)
    assert secret.startswith("pc_live_")
    assert row["key_fingerprint"] == minted.fingerprint
    assert await keys.verify(pool, secret) is not None


@postgres
async def test_opening_the_same_app_twice_leaves_one_row_and_one_live_token(pool: Pool) -> None:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="sandbox", name="support")
    await open_app(pool, VAULT, app, created_by="m_ana")
    await open_app(pool, VAULT, app, created_by="m_ben")
    [listed] = await apps_of(pool, org.id, "sandbox")
    assert listed.created_by == "m_ana"
    assert [key.revoked_at is None for key in await keys.listed(pool, org.id)] == [True, False]


@postgres
async def test_releases_are_numbered_from_one_and_come_back_newest_first(pool: Pool) -> None:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="production", name="support")
    await open_app(pool, VAULT, app, created_by="m_ana")
    source = checked_source(PROJECT)
    first = await keep_release(pool, app, source, author="m_ana", note="first")
    second = await keep_release(pool, app, source, author="k_ci", note="")
    assert (first.release, second.release) == (1, 2)
    assert (first.sha256, first.bytes) == (source.sha256, len(PROJECT))
    kept = await releases_of(pool, app)
    assert [(row.release, row.author, row.note) for row in kept] == [
        (2, "k_ci", ""),
        (1, "m_ana", "first"),
    ]
    [listed] = await apps_of(pool, org.id, "production")
    assert listed.release == 2
    assert (await source_of(pool, app, 1)).data == PROJECT


@postgres
async def test_each_world_hosts_its_own_apps(pool: Pool) -> None:
    org = await an_org(pool)
    await open_app(
        pool, VAULT, HostedApp(org=org.id, env="sandbox", name="support"), created_by="m_ana"
    )
    assert await apps_of(pool, org.id, "production") == []
    assert not await is_hosted(pool, HostedApp(org=org.id, env="production", name="support"))


@postgres
async def test_an_app_the_box_does_not_host_has_no_releases_to_keep_or_read(pool: Pool) -> None:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="production", name="nobody")
    with pytest.raises(NotFound, match="hosts no app called nobody"):
        await keep_release(pool, app, checked_source(PROJECT), author="m_ana", note="")
    with pytest.raises(NotFound, match="hosts no app called nobody"):
        await releases_of(pool, app)
    with pytest.raises(NotFound, match="no release 3"):
        await source_of(pool, app, 3)
    with pytest.raises(NotFound, match="hosts no app called nobody"):
        await drop_app(pool, app)


@postgres
async def test_dropping_an_app_takes_its_releases_and_revokes_its_token(pool: Pool) -> None:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="production", name="support")
    await open_app(pool, VAULT, app, created_by="m_ana")
    await keep_release(pool, app, checked_source(PROJECT), author="m_ana", note="")
    await drop_app(pool, app)
    assert await apps_of(pool, org.id, "production") == []
    [minted] = await keys.listed(pool, org.id)
    assert minted.revoked_at is not None
    async with pool.connection() as connection:
        counted = await connection.execute("SELECT count(*) AS n FROM hosted_releases")
        left = await counted.fetchone()
    assert left is not None
    assert left["n"] == 0


@postgres
async def test_removing_the_org_takes_its_hosted_apps_with_it(pool: Pool) -> None:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="production", name="support")
    await open_app(pool, VAULT, app, created_by="m_ana")
    await keep_release(pool, app, checked_source(PROJECT), author="m_ana", note="")
    await remove(pool, org.id)
    async with pool.connection() as connection:
        left = await (await connection.execute("SELECT count(*) AS n FROM hosted_apps")).fetchone()
    assert left is not None
    assert left["n"] == 0


APP = HostedApp(org="org_1", env="production", name="support")


# Two orgs, or two worlds, deploying the same sources under one name are two hosts: a name
# collides with no other on a machine two runners share.
def test_a_host_names_the_app_the_release_and_a_stamp_and_fits_a_host_name() -> None:
    host = host_of(APP, 7, "a" * 64, "")
    assert host.startswith("support-r7-")
    assert len(host) == len("support-r7-") + 8
    assert host_of(APP, 7, "a" * 64, "CRM_TOKEN@1.5") != host
    assert host_of(APP, 8, "a" * 64, "") != host
    assert (
        host_of(HostedApp(org="org_2", env="production", name="support"), 7, "a" * 64, "") != host
    )
    assert host_of(HostedApp(org="org_1", env="sandbox", name="support"), 7, "a" * 64, "") != host
    longest = HostedApp(org="org_1", env="production", name="x" * 40)
    assert len(host_of(longest, 123456, "a" * 64, "")) <= 63
    assert release_of(host) == 7
    assert release_of("support-r7") is None


def test_a_name_longer_than_a_host_can_carry_is_refused() -> None:
    assert checked_name("x" * 40) == "x" * 40
    with pytest.raises(DeclarationRefused, match="40 characters at most"):
        checked_name("x" * 41)
    with pytest.raises(DeclarationRefused, match="slug"):
        checked_name("Not A Slug")


@postgres
async def test_an_app_has_no_release_then_its_newest_under_a_host(pool: Pool) -> None:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="production", name="support")
    await open_app(pool, VAULT, app, created_by="m_ana")
    assert await hosted_in(pool, "production") == []
    [before] = await apps_of(pool, org.id, "production")
    assert (before.release, before.host, before.failed_why) == (None, None, None)
    await keep_release(pool, app, checked_source(PROJECT), author="m_ana", note="")
    [status] = await hosted_in(pool, "production")
    assert (status.org, status.name, status.release) == (org.id, "support", 1)
    assert status.host.startswith("support-r1-")
    assert (status.failed, status.live_host) == (False, None)
    assert await hosted_in(pool, "sandbox") == []


@postgres
async def test_a_secret_set_or_a_release_uploaded_is_a_new_host(pool: Pool) -> None:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="production", name="support")
    await open_app(pool, VAULT, app, created_by="m_ana")
    await keep_release(pool, app, checked_source(PROJECT), author="m_ana", note="")
    [first] = await hosted_in(pool, "production")
    secret = Secret(env="production", name="CRM_TOKEN", value="x")
    await put_secret(pool, VAULT, org.id, secret, set_by="m_ana")
    [with_secret] = await hosted_in(pool, "production")
    await keep_release(pool, app, checked_source(PROJECT), author="m_ana", note="")
    [second] = await hosted_in(pool, "production")
    assert len({first.host, with_secret.host, second.host}) == 3


@postgres
async def test_a_failure_is_the_wanted_hosts_until_a_newer_release_replaces_it(pool: Pool) -> None:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="production", name="support")
    await open_app(pool, VAULT, app, created_by="m_ana")
    await keep_release(pool, app, checked_source(PROJECT), author="m_ana", note="")
    [status] = await hosted_in(pool, "production")
    await failed(pool, app, status.host, why="npm install exited 1", runner="apps-1")
    [after] = await hosted_in(pool, "production")
    [listed] = await apps_of(pool, org.id, "production")
    assert after.failed
    assert listed.failed_why == "npm install exited 1"
    await keep_release(pool, app, checked_source(PROJECT), author="m_ana", note="")
    [newer] = await hosted_in(pool, "production")
    [relisted] = await apps_of(pool, org.id, "production")
    assert not newer.failed
    assert relisted.failed_why is None


@postgres
async def test_a_release_gone_live_is_what_serves_the_app_until_the_next_does(pool: Pool) -> None:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="production", name="support")
    await open_app(pool, VAULT, app, created_by="m_ana")
    await keep_release(pool, app, checked_source(PROJECT), author="m_ana", note="")
    [status] = await hosted_in(pool, "production")
    await went_live(pool, app, status.host, runner="apps-1")
    await keep_release(pool, app, checked_source(PROJECT), author="m_ana", note="")
    [newer] = await hosted_in(pool, "production")
    [listed] = await apps_of(pool, org.id, "production")
    assert (newer.release, newer.live_host, listed.live_release) == (2, status.host, 1)


@postgres
async def test_the_apps_token_opens_for_whoever_starts_it_and_not_for_an_app_not_hosted(
    pool: Pool,
) -> None:
    org = await an_org(pool)
    app = HostedApp(org=org.id, env="sandbox", name="support")
    await open_app(pool, VAULT, app, created_by="m_ana")
    token = await key_of(pool, VAULT, app)
    assert token.startswith("pc_test_")
    assert await keys.verify(pool, token) is not None
    with pytest.raises(NotFound, match="hosts no app called nobody"):
        await key_of(pool, VAULT, HostedApp(org=org.id, env="sandbox", name="nobody"))


def test_a_host_is_the_apps_whatever_its_release_and_never_a_longer_names() -> None:
    host = host_of(APP, 3, "a" * 64, "")
    assert is_a_host_of("support", host)
    rest = HostedApp(org="org_1", env="production", name="support-rest")
    assert not is_a_host_of("support", host_of(rest, 3, "a" * 64, ""))
    assert not is_a_host_of("support", "support-r3")

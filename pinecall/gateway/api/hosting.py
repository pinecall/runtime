"""The hosting doors: the apps the box hosts for an org, their releases, and the org's secrets."""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from pinecall.domain.names import parse_slug
from pinecall.gateway._deps import Acting, AppKey, GatewayDep, asked_by
from pinecall.postgres.pool import Pool
from pinecall.tenancy import admission, hosting, org_secrets
from pinecall.tenancy.hosting import HostedApp, Release
from pinecall.tenancy.org_secrets import Secret
from pinecall.wire.rest.hosting import (
    HostedAppList,
    HostedAppRow,
    PutSecretRequest,
    ReleaseList,
    ReleaseRow,
    SecretList,
    SecretRow,
)

router = APIRouter()


GZIP = "application/gzip"


@router.get("/v1/hosted")
async def list_hosted_apps(key: AppKey, gateway: GatewayDep) -> HostedAppList:
    """The apps the box hosts for the org in this world, by name."""
    listed = await hosting.apps_of(gateway.connections.pool, key.org, key.env)
    return HostedAppList(
        apps=[
            HostedAppRow(
                name=app.name,
                release=app.release,
                live_release=app.live_release,
                failed_why=app.failed_why,
                created_by=app.created_by,
                created_at=app.created_at.timestamp(),
            )
            for app in listed
        ]
    )


# The body is the tarball, no multipart; the note rides the query. An app's first release is
# what makes it: counted against the org's quota, and given the token its process will run on.
@router.post("/v1/hosted/{name}/releases")
async def upload_release(
    name: str,
    request: Request,
    key: AppKey,
    gateway: GatewayDep,
    note: Annotated[str, Query(max_length=200)] = "",
) -> ReleaseRow:
    """The project's sources as the app's next release."""
    pool = gateway.connections.pool
    app = HostedApp(org=key.org, env=key.env, name=parse_slug(name))
    source = await asyncio.to_thread(hosting.checked_source, await request.body())
    author = asked_by(key)
    if not await hosting.is_hosted(pool, app):
        hosted = len(await hosting.apps_of(pool, key.org, key.env))
        await admission.admit_hosted_app(pool, key.org, key.env, hosting=hosted)
        await hosting.open_app(pool, gateway.connections.vault, app, created_by=author)
    kept = await hosting.keep_release(pool, app, source, author=author, note=note.strip())
    return _release_row(app, kept)


@router.get("/v1/hosted/{name}/releases")
async def list_releases(name: str, key: AppKey, gateway: GatewayDep) -> ReleaseList:
    """The app's releases, newest first."""
    app = HostedApp(org=key.org, env=key.env, name=name)
    kept = await hosting.releases_of(gateway.connections.pool, app)
    return ReleaseList(releases=[_release_row(app, release) for release in kept])


@router.get("/v1/hosted/{name}/releases/{release}/source")
async def release_source(name: str, release: int, key: AppKey, gateway: GatewayDep) -> Response:
    """The tarball one release was uploaded as."""
    app = HostedApp(org=key.org, env=key.env, name=name)
    return Response(
        await hosting.source_of(gateway.connections.pool, app, release), media_type=GZIP
    )


@router.delete("/v1/hosted/{name}", status_code=204)
async def drop_hosted_app(name: str, key: AppKey, gateway: GatewayDep) -> None:
    """Stop hosting the app: its releases go, and its token is revoked."""
    app = HostedApp(org=key.org, env=key.env, name=name)
    await hosting.drop_app(gateway.connections.pool, app)


@router.get("/v1/secrets")
async def list_secrets(key: AppKey, gateway: GatewayDep) -> SecretList:
    """The org's secrets in this world, by name; never a value."""
    return await _secrets(gateway.connections.pool, key)


@router.put("/v1/secrets/{name}")
async def put_secret(
    name: str, body: PutSecretRequest, key: AppKey, gateway: GatewayDep
) -> SecretList:
    """Keep one secret, sealed, replacing the value it had; the org's list after it."""
    connections = gateway.connections
    secret = Secret(env=key.env, name=name, value=body.value)
    await org_secrets.put_secret(
        connections.pool, connections.vault, key.org, secret, set_by=asked_by(key)
    )
    return await _secrets(connections.pool, key)


@router.delete("/v1/secrets/{name}")
async def drop_secret(name: str, key: AppKey, gateway: GatewayDep) -> SecretList:
    """Forget one secret; the org's list after it, 404 for a name nobody set."""
    pool = gateway.connections.pool
    await org_secrets.drop_secret(pool, key.org, key.env, name)
    return await _secrets(pool, key)


async def _secrets(pool: Pool, key: Acting) -> SecretList:
    listed = await org_secrets.secrets_of(pool, key.org, key.env)
    return SecretList(
        secrets=[
            SecretRow(name=secret.name, set_by=secret.set_by, set_at=secret.set_at.timestamp())
            for secret in listed
        ]
    )


def _release_row(app: HostedApp, release: Release) -> ReleaseRow:
    return ReleaseRow(
        name=app.name,
        release=release.release,
        sha256=release.sha256,
        bytes=release.bytes,
        author=release.author,
        note=release.note,
        created_at=release.created_at.timestamp(),
    )

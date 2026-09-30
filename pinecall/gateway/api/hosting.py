"""The hosting doors: the apps the box hosts for an org, their releases, and the org's secrets."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from pinecall.domain.errors import DeclarationRefused
from pinecall.domain.names import parse_env
from pinecall.gateway._deps import Acting, AppKey, GatewayDep, asked_by
from pinecall.postgres.pool import Pool
from pinecall.tenancy import admission, hosted_running, hosting, org_secrets
from pinecall.tenancy.hosted_running import Served
from pinecall.tenancy.hosting import HostedApp, Release
from pinecall.tenancy.org_secrets import Secret
from pinecall.wire.rest.hosting import (
    AppLogsResponse,
    HostedAppList,
    HostedAppRow,
    PutSecretRequest,
    ReleaseList,
    ReleaseRow,
    RollbackRequest,
    SecretList,
    SecretRow,
    ServedPage,
    ServedRow,
)

router = APIRouter()


GZIP = "application/gzip"


ROLLED_BACK = "rollback to release {release}"


NOT_A_MONTH = "{month!r} is not a month: YYYY-MM, like 2026-09"


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
                stopped=app.stopped,
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
    app = HostedApp(org=key.org, env=key.env, name=hosting.checked_name(name))
    source = await asyncio.to_thread(hosting.checked_source, await _body(request))
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
    source = await hosting.source_of(gateway.connections.pool, app, release)
    return Response(source.data, media_type=GZIP)


@router.post("/v1/hosted/{name}/stop", status_code=204)
async def stop_hosted_app(name: str, key: AppKey, gateway: GatewayDep) -> None:
    """Stop running the app: its process drains, and its releases and token stay."""
    app = HostedApp(org=key.org, env=key.env, name=name)
    await hosted_running.stop_app(gateway.connections.pool, app, by=asked_by(key))


@router.post("/v1/hosted/{name}/start", status_code=204)
async def start_hosted_app(name: str, key: AppKey, gateway: GatewayDep) -> None:
    """Run a stopped app again, its newest release."""
    app = HostedApp(org=key.org, env=key.env, name=name)
    await hosted_running.start_app(gateway.connections.pool, app)


@router.post("/v1/hosted/{name}/rollback")
async def roll_back_release(
    name: str, body: RollbackRequest, key: AppKey, gateway: GatewayDep
) -> ReleaseRow:
    """An earlier release's sources kept again as the app's next release."""
    pool = gateway.connections.pool
    app = HostedApp(org=key.org, env=key.env, name=name)
    source = await hosting.source_of(pool, app, body.release)
    note = ROLLED_BACK.format(release=body.release)
    kept = await hosting.keep_release(pool, app, source, author=asked_by(key), note=note)
    return _release_row(app, kept)


# The runner sends them on its next beat: a first ask answers what it sent last, or nothing.
@router.get("/v1/hosted/{name}/logs")
async def app_logs(name: str, key: AppKey, gateway: GatewayDep) -> AppLogsResponse:
    """The last lines of the app's process, and the runner told to send them again."""
    app = HostedApp(org=key.org, env=key.env, name=name)
    logs = await hosted_running.ask_for_logs(gateway.connections.pool, app)
    return AppLogsResponse(
        name=name,
        host=logs.host,
        lines=logs.lines,
        at=None if logs.at is None else logs.at.timestamp(),
    )


@router.get("/v1/hosted/usage")
async def hosted_usage(
    key: AppKey, gateway: GatewayDep, month: Annotated[str | None, Query()] = None
) -> ServedPage:
    """The time the org's apps served here per UTC day, in one month: this one by default."""
    since, until = month_of(month)
    rows = await hosted_running.served(
        gateway.connections.pool, since, until, org=key.org, env=key.env
    )
    return served_page(since, until, rows)


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


def month_of(month: str | None) -> tuple[date, date]:
    """The first day of the month named (YYYY-MM), or of this UTC one, and of the next."""
    if month is None:
        first = datetime.now(UTC).date().replace(day=1)
    else:
        try:
            first = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC).date()
        except ValueError:
            raise DeclarationRefused(NOT_A_MONTH.format(month=month)) from None
    after = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, after


def served_page(since: date, until: date, rows: list[Served]) -> ServedPage:
    """The time apps served, as the doors answer it."""
    return ServedPage(
        since=since.isoformat(),
        until=until.isoformat(),
        rows=[
            ServedRow(
                org=row.org,
                env=parse_env(row.env),
                name=row.name,
                day=row.day.isoformat(),
                seconds=round(row.seconds, 1),
            )
            for row in rows
        ],
    )


# Read a chunk at a time and no further than one byte past the ceiling: an upload of any size
# costs the gateway the ceiling's memory, and `checked_source` refuses what passed it.
async def _body(request: Request) -> bytes:
    read = bytearray()
    async for chunk in request.stream():
        read += chunk
        if len(read) > hosting.LARGEST_SOURCE:
            break
    return bytes(read)


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

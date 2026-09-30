"""The runner's doors: the apps a world's runner is to have running, and what starts each."""

from fastapi import APIRouter, Response

from pinecall.domain.errors import NotAvailable
from pinecall.gateway._deps import Acting, GatewayDep, RunnerKey
from pinecall.gateway._gateway import Gateway
from pinecall.tenancy import hosting, org_secrets
from pinecall.tenancy.hosting import AppStatus, HostedApp
from pinecall.wire.rest.hosting import (
    AppEnvironment,
    RunnerHeartbeatRequest,
    RunnerHeartbeatResponse,
    RunnerReport,
    WantedApp,
)

router = APIRouter()


GZIP = "application/gzip"


NO_NAME = "this box has no name for {env}: a hosted app has no gateway address to dial"


# A report is kept only while the host it names is still the one wanted: what a newer release or
# a changed secret replaced says nothing about the app as it is now.
@router.post("/v1/runner/heartbeat")
async def runner_heartbeat(
    body: RunnerHeartbeatRequest, key: RunnerKey, gateway: GatewayDep
) -> RunnerHeartbeatResponse:
    """The runner's reports kept, and every app of its world it is to have running."""
    pool = gateway.connections.pool
    wanted = {
        (status.org, status.name): status for status in await hosting.hosted_in(pool, key.env)
    }
    for report in body.reports:
        status = wanted.get((report.org, report.name))
        if status is not None and status.host == report.host:
            await _kept(gateway, key, status, report, runner=body.runner)
    statuses = await hosting.hosted_in(pool, key.env) if body.reports else list(wanted.values())
    return RunnerHeartbeatResponse(
        world=key.env,
        apps=[
            WantedApp(
                org=status.org,
                name=status.name,
                release=status.release,
                sha256=status.sha256,
                host=status.host,
                registered=_is_registered(gateway, key, status),
                failed=status.failed_why is not None,
            )
            for status in statuses
            if status.release is not None and status.sha256 and status.host
        ],
    )


@router.get("/v1/runner/apps/{org}/{name}/releases/{release}/source")
async def runner_source(
    org: str, name: str, release: int, key: RunnerKey, gateway: GatewayDep
) -> Response:
    """The tarball of one release of any org's app in the runner's world."""
    app = HostedApp(org=org, env=key.env, name=name)
    return Response(
        await hosting.source_of(gateway.connections.pool, app, release), media_type=GZIP
    )


# The box's own names win over an org's secret of the same name: the door refuses those names.
@router.get("/v1/runner/apps/{org}/{name}/environment")
async def runner_environment(
    org: str, name: str, key: RunnerKey, gateway: GatewayDep
) -> AppEnvironment:
    """What the app's process is started with: the org's secrets, its token, the gateway."""
    connections = gateway.connections
    address = connections.settings.address_of(key.env)
    if address is None:
        raise NotAvailable(NO_NAME.format(env=key.env))
    app = HostedApp(org=org, env=key.env, name=name)
    secrets = await org_secrets.environment_of(connections.pool, connections.vault, org, key.env)
    token = await hosting.key_of(connections.pool, connections.vault, app)
    return AppEnvironment(environment={**secrets, "PINECALL_KEY": token, "PINECALL_URL": address})


async def _kept(
    gateway: Gateway, key: Acting, status: AppStatus, report: RunnerReport, *, runner: str
) -> None:
    pool = gateway.connections.pool
    app = HostedApp(org=status.org, env=key.env, name=status.name)
    if report.state == "live":
        await hosting.went_live(pool, app, status, runner=runner)
    else:
        await hosting.failed(pool, app, status, why=report.why, runner=runner)


def _is_registered(gateway: Gateway, key: Acting, status: AppStatus) -> bool:
    processes = gateway.live.processes_of(status.org, key.env)
    return any(process.host == status.host for process in processes)

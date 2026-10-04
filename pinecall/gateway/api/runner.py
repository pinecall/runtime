"""The runner's doors: the apps a world's runner is to have running, and what starts each."""

from datetime import UTC, datetime

from fastapi import APIRouter, Response

from pinecall.domain.errors import NotAvailable
from pinecall.domain.names import Env
from pinecall.gateway._deps import GatewayDep, RunnerKey
from pinecall.gateway._gateway import Gateway
from pinecall.postgres.pool import Pool
from pinecall.tenancy import hosted_running, hosting, org_secrets
from pinecall.tenancy.hosting import HostedApp
from pinecall.wire.rest.hosting import (
    AppEnvironment,
    RunnerHeartbeatRequest,
    RunnerHeartbeatResponse,
    RunnerReport,
    WantedApp,
)

router = APIRouter()


GZIP = "application/gzip"


NO_NAME = "this box has no PINECALL_DOMAIN: a hosted app has no gateway address to dial"


# In this order: what the runner says is kept first, so what it is answered already counts it.
@router.post("/v1/runner/heartbeat")
async def runner_heartbeat(
    body: RunnerHeartbeatRequest, key: RunnerKey, gateway: GatewayDep
) -> RunnerHeartbeatResponse:
    """The runner's reports and logs kept, the time served counted, and what it is to run."""
    pool = gateway.connections.pool
    for report in body.reports:
        await _kept(pool, HostedApp(org=report.org, env=key.env, name=report.name), report, body)
    for logs in body.logs:
        app = HostedApp(org=logs.org, env=key.env, name=logs.name)
        await hosted_running.keep_logs(pool, app, host=logs.host, lines=logs.lines)
    wanted = await hosting.hosted_in(pool, key.env)
    hosts = {app.org: _hosts_of(gateway, app.org, key.env) for app in wanted}
    serving = [
        (app.org, app.name)
        for app in wanted
        if any(hosting.is_a_host_of(app.name, host) for host in hosts[app.org])
    ]
    await hosted_running.count_serving(pool, key.env, serving, datetime.now(UTC))
    return RunnerHeartbeatResponse(
        world=key.env,
        apps=[
            WantedApp(
                org=app.org,
                name=app.name,
                release=app.release,
                sha256=app.sha256,
                host=app.host,
                registered=app.host in hosts[app.org],
                failed=app.failed,
                logs_wanted=app.logs_wanted,
                live_host=app.live_host,
            )
            for app in wanted
        ],
    )


@router.get("/v1/runner/apps/{org}/{name}/releases/{release}/source")
async def runner_source(
    org: str, name: str, release: int, key: RunnerKey, gateway: GatewayDep
) -> Response:
    """The tarball of one release of any org's app in the runner's world."""
    app = HostedApp(org=org, env=key.env, name=name)
    source = await hosting.source_of(gateway.connections.pool, app, release)
    return Response(source.data, media_type=GZIP)


# The box's own names win over an org's secret of the same name: the door refuses those names.
@router.get("/v1/runner/apps/{org}/{name}/environment")
async def runner_environment(
    org: str, name: str, key: RunnerKey, gateway: GatewayDep
) -> AppEnvironment:
    """What the app's process is started with: the org's secrets, its token, the gateway."""
    connections = gateway.connections
    address = connections.settings.address
    if address is None:
        raise NotAvailable(NO_NAME)
    app = HostedApp(org=org, env=key.env, name=name)
    secrets = await org_secrets.environment_of(connections.pool, connections.vault, org, key.env)
    token = await hosting.key_of(connections.pool, connections.vault, app)
    return AppEnvironment(environment={**secrets, "PINECALL_KEY": token, "PINECALL_URL": address})


async def _kept(
    pool: Pool, app: HostedApp, report: RunnerReport, body: RunnerHeartbeatRequest
) -> None:
    if report.state == "live":
        await hosting.went_live(pool, app, report.host, runner=body.runner)
    else:
        await hosting.failed(pool, app, report.host, why=report.why, runner=body.runner)


# What each of the org's processes in the world says it runs on: an app serves while one of them
# is a host of its own, whichever release, and that time is what the org is billed for.
def _hosts_of(gateway: Gateway, org: str, env: Env) -> frozenset[str]:
    processes = gateway.live.processes_of(org, env)
    return frozenset(process.host for process in processes if process.host is not None)
